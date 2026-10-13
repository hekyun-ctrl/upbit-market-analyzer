"""Offline tests: cached ticker samples are not candles and never bypass gaps."""
import json
import os
import tempfile
import unittest

from lbank_sample_store import load_snapshots, save_snapshots

SYMBOL = "BTC/USDT:USDT"


class CachedSampleTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.path = os.path.join(self.directory.name, "prices.json")

    def tearDown(self):
        self.directory.cleanup()

    def test_short_restart_restores_samples_and_cooldown(self):
        now = 2000000000
        samples = [(now - 120, 83200.), (now - 60, 83300.), (now, 83400.)]
        alert = {"day": "2026-10-13", "count": 2,
                 "last_time": now - 30, "last_side": "롱"}
        save_snapshots(self.path, SYMBOL, 60, samples, alert)
        observed, state = load_snapshots(self.path, SYMBOL, 60, now=now + 20)
        self.assertEqual(observed, samples)
        self.assertEqual(state, alert)

    def test_stale_history_is_dropped_without_losing_alert_cooldown(self):
        now = 2000000000
        alert = {"day": "2026-10-13", "count": 2,
                 "last_time": now - 300, "last_side": "숏"}
        save_snapshots(self.path, SYMBOL, 60, [(now - 3600, 83300.)], alert)
        observed, gate = load_snapshots(self.path, SYMBOL, 60, now=now)
        self.assertEqual(observed, [])
        self.assertEqual(gate, alert)

    def test_gap_rejects_older_part_of_history(self):
        now = 2000000000
        save_snapshots(self.path, SYMBOL, 60,
                       [(now - 650, 83300.), (now - 70, 83320.), (now, 83350.)], {})
        observed, _ = load_snapshots(self.path, SYMBOL, 60, now=now + 1)
        self.assertEqual(len(observed), 2)

    def test_wrong_symbol_and_corrupt_state_do_not_restore_prices(self):
        now = 2000000000
        save_snapshots(self.path, "ETH/USDT:USDT", 60, [(now, 3000.)], {})
        self.assertEqual(load_snapshots(self.path, SYMBOL, 60, now=now)[0], [])
        with open(self.path, "w", encoding="utf-8") as f:
            f.write("{broken")
        self.assertEqual(load_snapshots(self.path, SYMBOL, 60, now=now)[0], [])

    def test_future_timestamps_do_not_leak_into_live_observations(self):
        now = 2000000000
        save_snapshots(self.path, SYMBOL, 60, [(now + 10000, 84000.)], {})
        self.assertEqual(load_snapshots(self.path, SYMBOL, 60, now=now)[0], [])


if __name__ == "__main__":
    unittest.main()
