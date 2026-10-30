import json
import os
import tempfile
import unittest
import zlib

from lbank_native_candles import NativeCandleStore, decode_frame, parse_bar


def raw(start, period="1m", close=101., volume=2., updated=None):
    return {"a": "BTCUSDT", "b": period, "c": start, "d": "100",
            "e": str(close), "f": "103", "g": "98", "h": str(volume),
            "i": str(volume*100), "k": updated if updated is not None else start+59}


def frame(bar):
    return {"x": 2, "z": 4, "w": (bar["k"]+1)*1000, "d": bar}


class NativeCandlesTests(unittest.TestCase):
    def make_store(self, path=":memory:"):
        store = NativeCandleStore(path)
        self.addCleanup(store.db.close)
        return store

    def test_plain_and_compressed_wire_data(self):
        value = frame(raw(3000))
        encoded = json.dumps(value).encode()
        self.assertEqual(decode_frame(encoded), value)
        self.assertEqual(decode_frame(zlib.compress(encoded)), value)
        with self.assertRaises(ValueError):
            decode_frame(zlib.compress(b"a" * (2*1024*1024)))

    def test_subscription_snapshot_is_never_a_closed_bar(self):
        store = self.make_store()
        store.ingest(frame(raw(3000, "5m")), 1)
        self.assertEqual(store.db.execute("SELECT COUNT(*) FROM native_snapshots").fetchone()[0], 1)
        self.assertEqual(store.db.execute("SELECT COUNT(*) FROM native_bars").fetchone()[0], 0)

    def test_five_native_minutes_aggregate_only_after_next_minute(self):
        store = self.make_store()
        closed = []
        for i in range(6):
            t = 3000+i*60
            closed += store.ingest(frame(raw(t, updated=t+1)), t+1)
            closed += store.ingest(frame(raw(t, updated=t+59)), t+59)
        self.assertEqual(len(closed), 1)
        self.assertEqual(closed[0]["volume"], 10)
        self.assertEqual(closed[0]["open"], 100)
        self.assertEqual(closed[0]["high"], 103)
        self.assertEqual(closed[0]["low"], 98)
        self.assertEqual(closed[0]["close"], 101)
        self.assertEqual(store.status()["consecutive_closed_5m"], 1)
        self.assertEqual(store.status()["entry_source_status"], "see_NATIVE_ENTRY_STATUS")

    def test_repeated_updates_replace_volume_instead_of_adding(self):
        store = self.make_store()
        store.ingest(frame(raw(3000, volume=1, updated=3010)), 1)
        store.ingest(frame(raw(3000, volume=2, updated=3059)), 2)
        store.ingest(frame(raw(3000, volume=2, updated=3059)), 3)
        self.assertEqual(store.db.execute("SELECT volume FROM native_bars").fetchone()[0], 2)

    def test_gap_at_boundary_prevents_closure(self):
        store = self.make_store()
        store.ingest(frame(raw(3000)), 1)
        store.ingest(frame(raw(3060)), 25)
        self.assertEqual(store.db.execute("SELECT quality FROM native_bars WHERE start=3000").fetchone()[0], "gap")

    def test_missing_minute_is_not_synthetic_flat_candle(self):
        store = self.make_store()
        store.ingest(frame(raw(3000)), 1)
        store.ingest(frame(raw(3120)), 2)
        self.assertEqual(store.db.execute("SELECT COUNT(*) FROM native_bars WHERE start=3060").fetchone()[0], 0)
        self.assertEqual(store.status()["consecutive_closed_5m"], 0)

    def test_out_of_order_candle_and_old_update_are_ignored(self):
        store = self.make_store()
        store.ingest(frame(raw(3060, close=102, updated=3110)), 1)
        store.ingest(frame(raw(3000)), 2)
        store.ingest(frame(raw(3060, close=100, updated=3100)), 3)
        row = store.db.execute("SELECT * FROM native_bars").fetchone()
        self.assertEqual(row["close"], 102)
        self.assertEqual(row["start"], 3060)

    def test_restart_and_disconnect_do_not_certify_incomplete_bars(self):
        with tempfile.TemporaryDirectory() as directory:
            p = os.path.join(directory, "native.sqlite3")
            store = NativeCandleStore(p)
            store.ingest(frame(raw(3000)), 1)
            store.db.close()
            restored = NativeCandleStore(p)
            try:
                self.assertEqual(restored.db.execute("SELECT quality FROM native_bars").fetchone()[0], "gap")
                restored.ingest(frame(raw(3060)), 2)
                restored.disconnected()
                self.assertIsNone(restored.current)
                self.assertEqual(restored.db.execute("SELECT quality FROM native_bars WHERE start=3060").fetchone()[0], "gap")
            finally:
                restored.db.close()

    def test_invalid_symbol_geometry_nonfinite_or_unaligned_timestamp_rejected(self):
        for change in ({"a":"mBTCUSDT"}, {"b":"15m"}, {"c":3001},
                       {"f":"99"}, {"h":"-1"}, {"e":"NaN"}, {"k":3060}):
            with self.subTest(change=change), self.assertRaises(ValueError):
                parse_bar(dict(raw(3000), **change))

    def test_rsi_requires_contiguous_recent_history_and_never_switches_entries(self):
        store = self.make_store()
        with store.db:
            for i in range(250):
                p, t = 100+i, 3000+i*300
                store.db.execute("INSERT INTO native_bars VALUES(?,?,?,?,?,?,?,?,?,?)",
                    (300,t,p,p+1,p-1,p,1,p,t+299,"stream_closed"))
        store.server_time = 3000+250*300
        status = store.status()
        self.assertEqual(status["rsi14_wilder"],100)
        self.assertTrue(status["native_history_ready"])
        self.assertEqual(status["entry_source_status"], "see_NATIVE_ENTRY_STATUS")
        store.server_time += 300
        self.assertIsNone(store.status()["rsi14_wilder"])


if __name__ == "__main__":
    unittest.main()
