"""Observe 5m breakout candidates without creating trade recommendations.

The independent, deterministic early-stage audit is intentionally NOT a Telegram
signal, nor a ZB qualification override. Input is the same completed, contiguous
5m window the existing video alert worker already reads.
"""
from hashlib import sha256
import json
import math
from statistics import median

POLICY = "early_breakout_shadow_v1"
WINDOW = 6
BASELINE = 20
MIN_VOLUME_MULTIPLE = 1.5


def evaluate_early_breakout(bars):
    """Use only the newest completed bar and earlier completed bars.

    No future pivot, live partial candle, volume/current-bar lookahead, or
    guessed target/stop is allowed. Every call returns an auditable reason.
    """
    if len(bars) < BASELINE + 1:
        return {"status": "insufficient_history", "required": BASELINE + 1,
                "available": len(bars)}
    rows = bars[-(BASELINE + 1):]
    try:
        for i, row in enumerate(rows):
            if row.get("quality") != "stream_closed" or row.get("period", 300) != 300:
                raise ValueError("unverified_closed_candle")
            ts = row["start"]
            if type(ts) is not int or ts % 300 or (i and ts != rows[i-1]["start"] + 300):
                raise ValueError("non_contiguous_5m")
            o, h, l, c, v = (float(row[k]) for k in ("open", "high", "low", "close", "volume"))
            if not all(map(math.isfinite, (o, h, l, c, v))) or l <= 0 or v < 0 or not l <= min(o,c) <= max(o,c) <= h:
                raise ValueError("invalid_ohlcv")
            receipt = row.get("received_at")
            if type(receipt) not in (int, float) or receipt < ts + 300:
                raise ValueError("missing_closed_receipt")
    except (KeyError, TypeError, ValueError, OverflowError) as exc:
        return {"status": "invalid_source", "reason": str(exc) if isinstance(exc, ValueError) else type(exc).__name__}
    current = rows[-1]
    history = rows[:-1]
    baseline_volume = median(float(row["volume"]) for row in history)
    if baseline_volume <= 0:
        return {"status": "missing_volume_baseline", "start": current["start"]}
    previous_high = max(float(row["high"]) for row in history[-WINDOW:])
    previous_low = min(float(row["low"]) for row in history[-WINDOW:])
    close = float(current["close"])
    direction = "long" if close > previous_high else "short" if close < previous_low else None
    multiple = float(current["volume"]) / baseline_volume
    result = {"status": "no_breakout", "start": current["start"],
              "direction": direction, "close": close, "previous_high": previous_high,
              "previous_low": previous_low, "volume_multiple": round(multiple, 6),
              "baseline_volume": baseline_volume, "current_volume": float(current["volume"]),
              "volume_threshold": MIN_VOLUME_MULTIPLE, "bar_quality": "stream_closed"}
    if direction is not None:
        result["status"] = ("candidate" if multiple >= MIN_VOLUME_MULTIPLE
                            else "breakout_below_volume_threshold")
    return result


def persist_early_audit(ledger, bars, *, now, final_status, final_reasons):
    """Append one shadow observation per final 5m bar; NEVER call send().

    A missing or failed journal must not change the live entry decision.
    The first record is immutable so replays/restarts cannot rewrite history.
    """
    observed = evaluate_early_breakout(bars)
    if not bars:
        return observed
    start = bars[-1]["start"]
    fingerprint = sha256(json.dumps(
        [{k: b[k] for k in ("start", "open", "high", "low", "close", "volume", "quality")}
         for b in bars[-(BASELINE+1):]], sort_keys=True, separators=(",", ":"),
        allow_nan=False).encode()).hexdigest()
    evidence = dict(observation=observed, final_status=final_status,
                    final_reasons=final_reasons, input_sha256=fingerprint,
                    derived_from="lbank_native_1m_derived_5m",
                    source_limitation="stream_inferred_not_rest_verified",
                    delivery="observation_only_not_a_trade_signal")
    with ledger.db:
        ledger.db.execute("""CREATE TABLE IF NOT EXISTS video_early_breakout_audit (
            bar_start INTEGER PRIMARY KEY, policy TEXT NOT NULL,
            evaluated_at REAL NOT NULL, status TEXT NOT NULL,
            evidence TEXT NOT NULL)""")
        ledger.db.execute("""INSERT OR IGNORE INTO video_early_breakout_audit
            (bar_start, policy, evaluated_at, status, evidence) VALUES(?,?,?,?,?)""",
            (start, POLICY, now, observed["status"],
             json.dumps(evidence, ensure_ascii=False, sort_keys=True, allow_nan=False)))
    return observed
