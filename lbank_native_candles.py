"""Read-only native LBank futures candle recorder, independent of entry alerts.

Protocol observed in LBank's public futures web client on 2026-10-17.
The 5m subscription replies with a 5m snapshot, then PUSHES 1m candles.
Never relabel those pushes as 5m. Closed 5m bars require five consecutive
native 1m bars, closed by a continuous stream advancing to the next minute.
Stream closure is inferred, NOT a REST-verified final-candle flag.
The live recorder holds a just-ended minute briefly for late updates and
separately requested exchange snapshots before publishing it.
"""
import asyncio
import json
import logging
import math
import os
import sqlite3
import threading
import time
import zlib

import aiohttp

LOG = logging.getLogger("lbank_native_candles")
WS_URL = "wss://uuws.rerrkvifj.com/ws/v3"
MAX_BOUNDARY_DELAY = 15
MINUTE_REFERENCE_GRACE = 2


def consistent_final_minute(stream, final):
    """A finalized exchange snapshot must contain all observed trades."""
    extra_volume = final["volume"] - stream["volume"]
    extra_turnover = final["turnover"] - stream["turnover"]
    if (final["open"] != stream["open"] or final["high"] < stream["high"]
            or final["low"] > stream["low"] or extra_volume < 0):
        return False
    if extra_volume == 0:
        return all(final[key] == stream[key] for key in ("high", "low", "close", "turnover"))
    return (final["low"] * extra_volume - .000001 <= extra_turnover
            <= final["high"] * extra_volume + .000001)


def decode_frame(data):
    if isinstance(data, bytes):
        if not data.lstrip().startswith(b"{"):
            dec = zlib.decompressobj()
            data = dec.decompress(data, 1024 * 1024)
            if not dec.eof or dec.unconsumed_tail:
                raise ValueError("oversized or incomplete compressed frame")
        data = data.decode("utf-8")
    if len(data) > 1024 * 1024:
        raise ValueError("oversized frame")
    return json.loads(data)


def parse_bar(raw, *, observed_end_by=None):
    if raw.get("a") != "BTCUSDT" or raw.get("b") not in ("1m", "5m"):
        raise ValueError("wrong instrument or period")
    period = 60 if raw["b"] == "1m" else 300
    start, updated = int(raw["c"]), int(raw["k"])
    values = [float(raw[k]) for k in ("d", "f", "g", "e", "h", "i")]
    o, h, l, c, v, turnover = values
    if start <= 0 or start % period:
        raise ValueError("candle_start_unaligned")
    # A closing update of the minute already in flight can arrive just after
    # the boundary. Retain its actual update timestamp; never close it here.
    at_minute_end = (period == 60 and start+period <= updated <= start+period+MAX_BOUNDARY_DELAY
                     and observed_end_by is not None and updated <= observed_end_by)
    if not (start <= updated < start + period or at_minute_end):
        if updated == start+period:
            raise ValueError("candle_update_at_end")
        if updated < start:
            raise ValueError("candle_update_before_start")
        raise ValueError("candle_update_after_end")
    if not all(math.isfinite(x) for x in values):
        raise ValueError("candle_nonfinite_values")
    if min(o, h, l, c) <= 0 or min(v, turnover) < 0:
        raise ValueError("candle_nonpositive_price_or_negative_volume")
    if h < max(o, c, l) or l > min(o, c, h):
        raise ValueError("candle_invalid_ohlc_geometry")
    return (period, start, o, h, l, c, v, turnover, updated)


class NativeCandleStore:
    def __init__(self, path, *, reconcile_closes=False):
        if path != ":memory:":
            os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
        self.db = sqlite3.connect(path, timeout=2)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.executescript('''
          CREATE TABLE IF NOT EXISTS native_bars(
            period INTEGER, start INTEGER, open REAL, high REAL, low REAL,
            close REAL, volume REAL, turnover REAL, updated INTEGER,
            quality TEXT, PRIMARY KEY(period,start));
          CREATE TABLE IF NOT EXISTS native_snapshots(
            start INTEGER PRIMARY KEY, payload TEXT, server_ms INTEGER);
          CREATE TABLE IF NOT EXISTS native_arrivals(
            period INTEGER, start INTEGER, received INTEGER NOT NULL,
            source TEXT NOT NULL, PRIMARY KEY(period,start));
        ''')
        # Incomplete bars from a previous process cannot be certified.
        with self.db:
            self.db.execute("UPDATE native_bars SET quality='gap' WHERE quality='forming'")
        self.current = None
        self.last_seen = None
        self.reconcile_closes = reconcile_closes
        self.pending_close = None
        self.close_reference = None
        self.server_time = 0
        self.recovered_derived_5m = self.repair_derived_history()

    def repair_derived_history(self):
        """Recover ONLY complete previously sealed native 1m groups.

        Never infer missing 1m candles or promote forming/gap snapshots.
        Existing valid 5m bars are left unchanged.
        """
        recovered = 0
        with self.db:
            starts = self.db.execute("""
                SELECT (start - start % 300) AS bucket
                FROM native_bars
                WHERE period=60 AND quality='stream_closed'
                GROUP BY bucket
                HAVING COUNT(*)=5
                ORDER BY bucket
            """).fetchall()
            for row in starts:
                start = row["bucket"]
                exists = self.db.execute(
                    "SELECT 1 FROM native_bars WHERE period=300 AND start=? AND quality='stream_closed'",
                    (start,)).fetchone()
                if exists:
                    continue
                if self._aggregate(start) is not None:
                    recovered += 1
        if recovered:
            LOG.info("NATIVE_DERIVED_HISTORY_REPAIRED recovered=%d source=sealed_native_1m", recovered)
        return recovered

    def disconnected(self):
        with self.db:
            self.db.execute("UPDATE native_bars SET quality='gap' WHERE period=60 AND start IN (?,?) "
                            "AND quality='forming'", (self.current, self.pending_close))
        self.current, self.last_seen = None, None
        self.pending_close, self.close_reference = None, None

    def reference_minute(self, reference, wall_received):
        """Use both ticketed replies only while the corresponding minute is mutable."""
        start = reference["start"]
        if (not self.reconcile_closes or start not in (self.current, self.pending_close)
                or wall_received < start + 60 or wall_received > start + 60 + MINUTE_REFERENCE_GRACE
                or len(set(reference.get("corroborated_routes", ()))) != 2):
            return
        self.close_reference = reference

    def flush_minute(self, wall_received):
        with self.db:
            return self._publish_pending(wall_received)

    def _publish_pending(self, wall_received):
        start = self.pending_close
        if start is None:
            return []
        reference = self.close_reference
        if reference is not None and reference["start"] != start:
            reference = None
        # A successor and two identical early replies can precede the last
        # update to the prior minute. Do not publish before this deadline.
        if self.reconcile_closes and wall_received < start + 60 + MINUTE_REFERENCE_GRACE:
            return []
        source = 'lbank_native_stream_1m'
        if reference is not None:
            prior = self.db.execute("SELECT * FROM native_bars WHERE period=60 AND start=?", (start,)).fetchone()
            fields = ("open", "high", "low", "close", "volume", "turnover")
            final = {k: float(reference[k]) for k in fields}
            if reference["updated"] < prior["updated"]:
                LOG.info("NATIVE_MINUTE_CLOSE_REFERENCE start=%d status=stream_newer", start)
            elif consistent_final_minute(prior, final):
                source = 'lbank_native_stream_1m_with_final_reply'
                self.db.execute("UPDATE native_bars SET open=?,high=?,low=?,close=?,volume=?,turnover=?,"
                                "updated=? WHERE period=60 AND start=?",
                                (*(final[k] for k in fields), reference["updated"], start))
                LOG.info("NATIVE_MINUTE_CLOSE_REFERENCE start=%d status=%s stream_volume=%.4f final_volume=%.4f",
                         start, "reconciled" if final["volume"] > prior["volume"] else "matched",
                         prior["volume"], final["volume"])
            else:
                LOG.warning("NATIVE_MINUTE_CLOSE_REFERENCE start=%d status=conflict", start)
            self.close_reference = None
        self.pending_close = None
        self.db.execute("UPDATE native_bars SET quality='stream_closed' WHERE period=60 AND start=?",
                        (start,))
        self.db.execute("INSERT OR IGNORE INTO native_arrivals VALUES(?,?,?,?)",
                        (60, start, int(wall_received), source))
        finished = self._aggregate(start // 300 * 300)
        if finished is None:
            return []
        self.db.execute("INSERT OR IGNORE INTO native_arrivals VALUES(?,?,?,?)",
                        (300, finished['start'], int(wall_received), 'lbank_native_1m_derived_5m'))
        cutoff = self.server_time - 32 * 86400
        self.db.execute("DELETE FROM native_bars WHERE start<?", (cutoff,))
        self.db.execute("DELETE FROM native_snapshots WHERE start<?", (cutoff,))
        self.db.execute("DELETE FROM native_arrivals WHERE start<?", (cutoff,))
        return [finished]

    def _write_forming(self, bar):
        self.db.execute('''INSERT INTO native_bars VALUES(?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(period,start) DO UPDATE SET
            open=excluded.open,high=excluded.high,low=excluded.low,close=excluded.close,
            volume=excluded.volume,turnover=excluded.turnover,updated=excluded.updated,
            quality=excluded.quality''', (*bar, "forming"))

    def ingest(self, frame, received, wall_received=None):
        if frame.get("x") != 2 or frame.get("z") not in (3, 4):
            return []
        self.server_time = max(self.server_time, int(frame["w"]) / 1000)
        raw_rows = frame.get("d", [])
        if isinstance(raw_rows, dict):
            raw_rows = [raw_rows]
        wall_received = time.time() if wall_received is None else wall_received
        observed_end_by = min(wall_received, int(frame["w"]) / 1000)
        parsed = []
        for raw in raw_rows:
            active = False
            if raw.get("a") == "BTCUSDT" and raw.get("b") == "1m":
                start = int(raw["c"])
                if self.current is not None and start < self.current and start != self.pending_close:
                    # A revision to an older minute cannot change a sealed bar.
                    continue
                if self.current is None and start+60 <= min(observed_end_by, int(raw["k"])):
                    # A late row on reconnect is not a fresh contiguous start.
                    continue
                active = start in (self.current, self.pending_close)
            try:
                parsed.append((raw, parse_bar(raw, observed_end_by=observed_end_by if active else None)))
            except ValueError as exc:
                if str(exc) in {"candle_update_after_end", "candle_update_at_end"}:
                    # A late revision is unusable, but must not disconnect the
                    # stream and destroy the continuity of later valid minutes.
                    LOG.info("NATIVE_CANDLES_LATE_ROW_SKIPPED active=%s period=%s offset_s=%s receipt_offset_s=%s",
                             active, "1m" if raw.get("b") == "1m" else "5m",
                             int(raw["k"])-int(raw["c"]), wall_received-int(raw["c"]))
                    continue
                raise
        closed = []
        with self.db:
            closed.extend(self._publish_pending(wall_received))
            for raw, bar in parsed:
                period, start, o, h, l, c, v, turnover, updated = bar
                if updated > self.server_time + 2:
                    raise ValueError("candle ahead of server clock")
                if period == 300:
                    self.db.execute("INSERT OR REPLACE INTO native_snapshots VALUES(?,?,?)",
                                    (start, json.dumps(raw), int(frame["w"])))
                    continue  # Snapshot is still forming, never usable as a close.
                if start == self.pending_close:
                    prior = self.db.execute("SELECT * FROM native_bars WHERE period=60 AND start=?",
                                            (start,)).fetchone()
                    later = dict(open=o, high=h, low=l, close=c, volume=v, turnover=turnover)
                    if updated >= prior["updated"] and consistent_final_minute(prior, later):
                        self._write_forming(bar)
                        LOG.info("NATIVE_MINUTE_LATE_UPDATE start=%d stream_volume=%.4f late_volume=%.4f",
                                 start, prior["volume"], v)
                    continue  # Do not change the newer minute or its continuity clock.
                previous = self.db.execute("SELECT * FROM native_bars WHERE period=60 AND start=?",
                                           (self.current,)).fetchone()
                if self.current is not None and start < self.current:
                    continue
                if previous and start == self.current and updated < previous["updated"]:
                    continue
                if previous and start > self.current:
                    clean = (start == self.current + 60 and self.last_seen is not None
                             and 0 <= received - self.last_seen <= 15)
                    if clean:
                        self.pending_close = self.current
                        closed.extend(self._publish_pending(wall_received))
                    else:
                        self.db.execute("UPDATE native_bars SET quality='gap' WHERE period=60 AND start=?",
                                        (self.current,))
                        self.close_reference = None
                self._write_forming(bar)
                self.current, self.last_seen = start, received
        return closed

    def _aggregate(self, start):
        rows = self.db.execute('''SELECT * FROM native_bars WHERE period=60
            AND start>=? AND start<? ORDER BY start''', (start, start+300)).fetchall()
        if (len(rows) != 5 or [r["start"] for r in rows] != list(range(start, start+300, 60))
                or any(r["quality"] != "stream_closed" for r in rows)):
            return None
        bar = (300, start, rows[0]["open"], max(r["high"] for r in rows),
               min(r["low"] for r in rows), rows[-1]["close"],
               sum(r["volume"] for r in rows), sum(r["turnover"] for r in rows),
               rows[-1]["updated"], "stream_closed")
        self.db.execute("INSERT OR REPLACE INTO native_bars VALUES(?,?,?,?,?,?,?,?,?,?)", bar)
        return dict(zip(("period", "start", "open", "high", "low", "close", "volume",
                         "turnover", "updated", "quality"), bar))

    def status(self):
        rows = self.db.execute("SELECT start,close FROM native_bars WHERE period=300 AND quality='stream_closed' ORDER BY start DESC LIMIT 250").fetchall()
        count = 0
        expected = int(self.server_time // 300) * 300 - 300
        for row in rows:
            if row["start"] != expected:
                break
            count += 1
            expected -= 300
        rsi14 = None
        if count >= 250:
            prices = [r["close"] for r in reversed(rows)]
            changes = [b-a for a, b in zip(prices, prices[1:])]
            gain = sum(max(x, 0) for x in changes[:14]) / 14
            loss = sum(max(-x, 0) for x in changes[:14]) / 14
            for change in changes[14:]:
                gain = (13*gain + max(change, 0)) / 14
                loss = (13*loss + max(-change, 0)) / 14
            rsi14 = 50. if gain == loss == 0 else 100. if loss == 0 else 100-100/(1+gain/loss)
        total = self.db.execute("SELECT COUNT(*) FROM native_bars WHERE period=300 AND quality='stream_closed'").fetchone()[0]
        latest = self.db.execute("SELECT MAX(start) FROM native_bars WHERE period=300 AND quality='stream_closed'").fetchone()[0]
        return {"consecutive_closed_5m": count, "stored_closed_5m": total,
                "latest_stored_5m": latest, "expected_latest_5m": int(self.server_time // 300) * 300 - 300,
                "recovered_derived_5m": self.recovered_derived_5m,
                "warmup_target": 250, "rsi14_wilder": rsi14,
                "native_history_ready": count >= 250, "entry_source_status": "see_NATIVE_ENTRY_STATUS",
                "source": "lbank_native_1m_aggregated_5m",
                "closure": "stream_inferred_not_rest_verified"}


class NativeCandleThread(threading.Thread):
    def __init__(self, path=None):
        super().__init__(name="lbank-native-candles", daemon=True)
        self.path = path or os.getenv("LBANK_NATIVE_CANDLE_DB", "/data/lbank_native_candles.sqlite3")

    def run(self):
        while True:
            try:
                asyncio.run(self._run())
                return
            except Exception as exc:
                LOG.error("NATIVE_CANDLES_RESTART exception=%s retry_seconds=30", type(exc).__name__)
                time.sleep(30)

    async def _heartbeat(self, ws):
        while True:
            await asyncio.sleep(6)
            await ws.send_json({"action": "ping", "ping": str(int(time.time()*1000))})

    async def _final_minutes(self, session, store):
        # candle_evidence also decodes the native recorder's wire format.
        from lbank_zb.candle_evidence import ReferenceDisagreement, capture_reference

        while True:
            end = (int(time.time()) // 60 + 1) * 60
            await asyncio.sleep(max(0, end - 6 - time.time()))
            deadline = end + MINUTE_REFERENCE_GRACE
            try:
                reference = await asyncio.wait_for(
                    capture_reference(session, end, period=60, grace=MINUTE_REFERENCE_GRACE),
                    timeout=max(0, deadline - time.time()))
            except (ReferenceDisagreement, OSError, TimeoutError, ValueError, aiohttp.ClientError):
                reference = None
            if reference:
                store.reference_minute(reference, time.time())
            await asyncio.sleep(max(0, deadline - time.time()))
            try:
                for bar in store.flush_minute(time.time()):
                    LOG.info("NATIVE_CANDLE_CLOSED %s", json.dumps(bar))
            except sqlite3.Error as exc:
                LOG.warning("NATIVE_MINUTE_CLOSE_STORAGE_FAILURE exception=%s", type(exc).__name__)

    async def _run(self):
        store = NativeCandleStore(self.path, reconcile_closes=True)
        delay = 2
        while True:
            heartbeat = finalizer = None
            try:
                async with aiohttp.ClientSession(
                    trust_env=True, headers={"Origin": "https://www.lbank.com"}
                ) as session:
                    async with session.ws_connect(WS_URL, timeout=12, receive_timeout=20,
                                                  max_msg_size=1024*1024) as ws:
                        await ws.send_json({"x": 2, "y": "2000000001", "z": 1,
                            "a": {"i": "BTCUSDT_5m"},
                            "e": json.dumps({"bvc": "202", "isUsd": 1})})
                        heartbeat = asyncio.create_task(self._heartbeat(ws))
                        finalizer = asyncio.create_task(self._final_minutes(session, store))
                        LOG.info("NATIVE_CANDLES_CONNECTED entry_readiness=see_NATIVE_ENTRY_STATUS")
                        next_log = 0
                        async for msg in ws:
                            if msg.type in (aiohttp.WSMsgType.TEXT, aiohttp.WSMsgType.BINARY):
                                frame = decode_frame(msg.data)
                                for bar in store.ingest(frame, time.monotonic()):
                                    LOG.info("NATIVE_CANDLE_CLOSED %s", json.dumps(bar))
                                if frame.get("x") == 2 and frame.get("z") in (3, 4):
                                    delay = 2
                                    if time.monotonic() >= next_log:
                                        LOG.info("NATIVE_CANDLES_STATUS %s", json.dumps(store.status()))
                                        next_log = time.monotonic() + 60
                            elif msg.type == aiohttp.WSMsgType.ERROR:
                                raise RuntimeError("websocket error")
            except Exception as exc:
                safe_reason = str(exc) if str(exc) in (
                    "oversized or incomplete compressed frame", "oversized frame",
                    "wrong instrument or period", "candle_start_unaligned",
                    "candle_update_at_end", "candle_update_before_start", "candle_update_after_end",
                    "candle_nonfinite_values", "candle_nonpositive_price_or_negative_volume",
                    "candle_invalid_ohlc_geometry",
                    "candle ahead of server clock", "websocket error") else "unclassified"
                LOG.warning("NATIVE_CANDLES_RECONNECT exception=%s reason=%s retry_seconds=%s",
                            type(exc).__name__, safe_reason, delay)
            finally:
                tasks = [task for task in (heartbeat, finalizer) if task is not None]
                for task in tasks:
                    task.cancel()
                await asyncio.gather(*tasks, return_exceptions=True)
                store.disconnected()
            await asyncio.sleep(delay)
            delay = min(delay * 2, 60)
