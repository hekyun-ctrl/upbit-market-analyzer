"""Read-only native LBank futures candle recorder, independent of entry alerts.

Protocol observed in LBank's public futures web client on 2026-10-17.
The 5m subscription replies with a 5m snapshot, then PUSHES 1m candles.
Never relabel those pushes as 5m. Closed 5m bars require five consecutive
native 1m bars, closed by a continuous stream advancing to the next minute.
Stream closure is inferred, NOT a REST-verified final-candle flag.
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


def parse_bar(raw):
    if raw.get("a") != "BTCUSDT" or raw.get("b") not in ("1m", "5m"):
        raise ValueError("wrong instrument or period")
    period = 60 if raw["b"] == "1m" else 300
    start, updated = int(raw["c"]), int(raw["k"])
    values = [float(raw[k]) for k in ("d", "f", "g", "e", "h", "i")]
    o, h, l, c, v, turnover = values
    if (start <= 0 or start % period or not start <= updated < start + period
            or not all(math.isfinite(x) for x in values)
            or min(o, h, l, c) <= 0 or min(v, turnover) < 0
            or h < max(o, c, l) or l > min(o, c, h)):
        raise ValueError("invalid candle geometry or timestamp")
    return (period, start, o, h, l, c, v, turnover, updated)


class NativeCandleStore:
    def __init__(self, path):
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
        self.server_time = 0

    def disconnected(self):
        if self.current is not None:
            with self.db:
                self.db.execute("UPDATE native_bars SET quality='gap' WHERE period=60 AND start=?",
                                (self.current,))
        self.current, self.last_seen = None, None

    def ingest(self, frame, received, wall_received=None):
        if frame.get("x") != 2 or frame.get("z") not in (3, 4):
            return []
        self.server_time = max(self.server_time, int(frame["w"]) / 1000)
        raw_rows = frame.get("d", [])
        if isinstance(raw_rows, dict):
            raw_rows = [raw_rows]
        parsed = [(raw, parse_bar(raw)) for raw in raw_rows]
        wall_received = int(time.time() if wall_received is None else wall_received)
        closed = []
        with self.db:
            for raw, bar in parsed:
                period, start, o, h, l, c, v, turnover, updated = bar
                if updated > self.server_time + 2:
                    raise ValueError("candle ahead of server clock")
                if period == 300:
                    self.db.execute("INSERT OR REPLACE INTO native_snapshots VALUES(?,?,?)",
                                    (start, json.dumps(raw), int(frame["w"])))
                    continue  # Snapshot is still forming, never usable as a close.
                previous = self.db.execute("SELECT * FROM native_bars WHERE period=60 AND start=?",
                                           (self.current,)).fetchone()
                if self.current is not None and start < self.current:
                    continue
                if previous and start == self.current and updated < previous["updated"]:
                    continue
                if previous and start > self.current:
                    clean = (start == self.current + 60 and self.last_seen is not None
                             and 0 <= received - self.last_seen <= 15)
                    self.db.execute("UPDATE native_bars SET quality=? WHERE period=60 AND start=?",
                                    ("stream_closed" if clean else "gap", self.current))
                    if clean:
                        self.db.execute("INSERT OR IGNORE INTO native_arrivals VALUES(?,?,?,?)",
                                        (60, self.current, wall_received, 'lbank_native_stream_1m'))
                    finished = self._aggregate(self.current // 300 * 300)
                    if finished is not None:
                        closed.append(finished)
                        self.db.execute("INSERT OR IGNORE INTO native_arrivals VALUES(?,?,?,?)",
                                        (300, finished['start'], wall_received, 'lbank_native_1m_derived_5m'))
                self.db.execute('''INSERT INTO native_bars VALUES(?,?,?,?,?,?,?,?,?,?)
                    ON CONFLICT(period,start) DO UPDATE SET
                    open=excluded.open,high=excluded.high,low=excluded.low,close=excluded.close,
                    volume=excluded.volume,turnover=excluded.turnover,updated=excluded.updated,
                    quality=excluded.quality''', (*bar, "forming"))
                self.current, self.last_seen = start, received
            if closed:
                cutoff = self.server_time - 32 * 86400
                self.db.execute("DELETE FROM native_bars WHERE start<?", (cutoff,))
                self.db.execute("DELETE FROM native_snapshots WHERE start<?", (cutoff,))
                self.db.execute("DELETE FROM native_arrivals WHERE start<?", (cutoff,))
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
        return {"consecutive_closed_5m": count, "warmup_target": 250, "rsi14_wilder": rsi14,
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

    async def _run(self):
        store = NativeCandleStore(self.path)
        delay = 2
        while True:
            heartbeat = None
            try:
                async with aiohttp.ClientSession(trust_env=True) as session:
                    async with session.ws_connect(WS_URL, timeout=12, receive_timeout=20,
                                                  max_msg_size=1024*1024) as ws:
                        await ws.send_json({"x": 2, "y": "2000000001", "z": 1,
                            "a": {"i": "BTCUSDT_5m"},
                            "e": json.dumps({"bvc": "202", "isUsd": 1})})
                        heartbeat = asyncio.create_task(self._heartbeat(ws))
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
                LOG.warning("NATIVE_CANDLES_RECONNECT exception=%s retry_seconds=%s",
                            type(exc).__name__, delay)
            finally:
                if heartbeat:
                    heartbeat.cancel()
                    await asyncio.gather(heartbeat, return_exceptions=True)
                store.disconnected()
            await asyncio.sleep(delay)
            delay = min(delay * 2, 60)
