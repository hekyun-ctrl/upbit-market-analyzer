"""Read a consistent, read-only native-candle snapshot for entry reviews.

No ticker history fallback. Closed 1m closes preserve existing short-horizon
strategy timing; the current quote is separate and never appended as a bar.
5m RSI and aligned 15m context use only complete native bars. Stream-inferred
closure is not an exchange REST final flag or a chart-parity certification.
"""
import math
import os
import sqlite3
from pathlib import Path

from lbank_sampled_context import closed_context_gate

SOURCE = "lbank_native_1m_aggregated_5m"
MIN_FIVE_BARS = 20  # Existing sampled RSI required at least 20 closed 5m buckets.


def wilder_rsi_series(closes, period=14):
    if len(closes) <= period:
        return []
    changes = [b - a for a, b in zip(closes, closes[1:])]
    gain = sum(max(c, 0) for c in changes[:period]) / period
    loss = sum(max(-c, 0) for c in changes[:period]) / period

    def value():
        return 50. if gain == loss == 0 else 100. if loss == 0 else 100. - 100. / (1. + gain / loss)

    result = [value()]
    for change in changes[period:]:
        gain = ((period - 1) * gain + max(change, 0)) / period
        loss = ((period - 1) * loss + max(-change, 0)) / period
        result.append(value())
    return result


def recent_contiguous(rows, expected, period):
    result = []
    for row in rows:
        if row["start"] != expected:
            break
        values = [row[k] for k in ("open", "high", "low", "close", "volume", "turnover")]
        o, h, l, c, v, turnover = values
        if (row["start"] % period or row["quality"] != "stream_closed"
                or not all(math.isfinite(v) for v in values)
                or min(o, h, l, c) <= 0 or min(v, turnover) < 0
                or h < max(o, c, l) or l > min(o, c, h)
                # The native feed can report a sealed minute's last revision
                # just after its boundary (up to MAX_BOUNDARY_DELAY).
                # A derived 5m inherits that final minute timestamp.
                # Only writer-certified stream_closed bars are accepted.
                or not expected <= row["updated"] <= expected + period + 15):
            break
        result.append(dict(row))
        expected -= period
    return list(reversed(result))


class NativeEntryFeed:
    def __init__(self, path=None):
        self.path = path or os.getenv("LBANK_NATIVE_CANDLE_DB", "/data/lbank_native_candles.sqlite3")

    def snapshot(self, now, quote):
        """Return (state, diagnostics); never create/repair the writer's DB."""
        try:
            if not (math.isfinite(now) and now > 0 and math.isfinite(quote) and quote > 0):
                raise ValueError("invalid quote or clock")
            expected1, expected5 = int(now // 60) * 60 - 60, int(now // 300) * 300 - 300
            uri = Path(self.path).absolute().as_uri() + "?mode=ro"
            db = sqlite3.connect(uri, uri=True, timeout=1)
            try:
                db.row_factory = sqlite3.Row
                db.execute("PRAGMA query_only=ON")
                db.execute("BEGIN")
                # Include gap/forming rows so a bad latest bar cannot be skipped.
                has_arrivals = db.execute(
                    "SELECT 1 FROM sqlite_master WHERE type='table' AND name='native_arrivals'"
                ).fetchone() is not None
                if has_arrivals:
                    query = """
                        SELECT b.*, a.received AS received_at FROM native_bars b
                        LEFT JOIN native_arrivals a ON a.period=b.period AND a.start=b.start
                            AND a.source=?
                        WHERE b.period=? AND b.start<=?
                        ORDER BY b.start DESC LIMIT ?
                    """
                    rows1 = db.execute(query, ("lbank_native_stream_1m", 60, expected1, 360)).fetchall()
                    rows5 = db.execute(query, ("lbank_native_1m_derived_5m", 300, expected5, 300)).fetchall()
                else:
                    # Older stores can still feed the existing entry path. They
                    # cannot certify the time a candle became observable.
                    query = """
                        SELECT *, NULL AS received_at FROM native_bars
                        WHERE period=? AND start<=? ORDER BY start DESC LIMIT ?
                    """
                    rows1 = db.execute(query, (60, expected1, 360)).fetchall()
                    rows5 = db.execute(query, (300, expected5, 300)).fetchall()
            finally:
                db.close()
            minutes = recent_contiguous(rows1, expected1, 60)
            # Preserve the original 250-bar live RSI window exactly. The wider
            # read is ONLY for an independent 1h public-technique paper study.
            five = recent_contiguous(rows5[:250], expected5, 300)
            research_five = recent_contiguous(rows5, expected5, 300)
            # Only candle timestamps/quality are logged; never expose quote or
            # trading data in operational health diagnostics.
            latest_one = rows1[0] if rows1 else None
            latest_five = rows5[0] if rows5 else None
            diag = {"source": SOURCE, "closed_1m": len(minutes), "closed_5m": len(five),
                    "required_5m": MIN_FIVE_BARS, "rsi_warmup_target": 250,
                    "rsi_history_mature": len(five) >= 250,
                    "expected_1m_start": expected1,
                    "latest_1m_start": latest_one["start"] if latest_one else None,
                    "latest_1m_quality": latest_one["quality"] if latest_one else None,
                    "latest_1m_update_offset": latest_one["updated"] - latest_one["start"] if latest_one else None,
                    "expected_5m_start": expected5,
                    "latest_5m_start": latest_five["start"] if latest_five else None,
                    "latest_5m_quality": latest_five["quality"] if latest_five else None,
                    "closure": "stream_inferred_not_rest_verified"}
            if len(minutes) < 65 or len(five) < MIN_FIVE_BARS:
                return None, dict(diag, reason="native_history_missing_stale_or_gapped", entry_source_switched=False)
            # Native 15m close exists only with all three aligned 5m bars.
            by_start = {b["start"]: b for b in five}
            final15 = int(now // 900) * 900 - 900
            fifteen = []
            for start in range(final15 - 4 * 900, final15 + 1, 900):
                if any(start + offset not in by_start for offset in (0, 300, 600)):
                    return None, dict(diag, reason="native_15m_incomplete", entry_source_switched=False)
                fifteen.append(by_start[start + 600]["close"])
            closes5 = [b["close"] for b in five]
            # Never expand a 5m bar into five fake minute closes, append a quote
            # as an extra minute, or interpolate missing data.
            state = {"minute_bars": minutes, "five_bars": five,
                     "research_five_bars": research_five,
                     "prices": [b["close"] for b in minutes], "quote": quote,
                     "closes15": fifteen, "last5_start": five[-1]["start"],
                     "rsi5": {"closes": closes5, "rsi": wilder_rsi_series(closes5)},
                     "source": SOURCE, "quote_observed_at": now}
            return state, dict(diag, reason="native_ready", entry_source_switched=True,
                               last5_start=five[-1]["start"], rsi14_wilder=state["rsi5"]["rsi"][-1])
        except (OSError, sqlite3.Error, ValueError, TypeError, KeyError, OverflowError):
            return None, {"source": SOURCE, "reason": "native_store_unavailable_or_invalid",
                          "entry_source_switched": False}


def native_context_gate(state, direction, strategy):
    if not state:
        return False, "native_data_unavailable", None
    return closed_context_gate(state["rsi5"]["closes"][-8:], state["closes15"],
                               direction, strategy, context_type=SOURCE)
