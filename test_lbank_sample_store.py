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
                 "last_time": now - 30, "last_side": "롱", "last_reversal_preview": now - 60}
        save_snapshots(self.path, SYMBOL, 60, samples, alert)
        observed, state = load_snapshots(self.path, SYMBOL, 60, now=now + 20)
        self.assertEqual(observed, samples)
        self.assertEqual(state, alert)

    def test_stale_history_is_dropped_without_losing_alert_cooldown(self):
        now = 2000000000
        alert = {"day": "2026-10-13", "count": 2,
                 "last_time": now - 300, "last_side": "숏", "last_reversal_preview": None}
        save_snapshots(self.path, SYMBOL, 60, [(now - 3600, 83300.)], alert)
        observed, gate = load_snapshots(self.path, SYMBOL, 60, now=now)
        self.assertEqual(observed, [])
        self.assertEqual(gate, alert)

    def test_old_cache_without_preview_field_still_loads_safely(self):
        now = 2000000000
        old = {"day": "2026-10-13", "count": 1,
               "last_time": now - 70, "last_side": "숏"}
        save_snapshots(self.path, SYMBOL, 60, [(now - 60, 83400.), (now, 83420.)], old)
        observed, gate = load_snapshots(self.path, SYMBOL, 60, now=now + 20)
        self.assertEqual(len(observed), 2)
        self.assertIsNone(gate["last_reversal_preview"])
        self.assertEqual(gate["last_side"], "숏")

    def test_invalid_future_reversal_notice_timestamp_discarded(self):
        now = 2000000000
        alert = {"day": "2026-10-13", "count": 1,
                 "last_time": now - 120, "last_side": "숏",
                 "last_reversal_preview": now + 3000}
        save_snapshots(self.path, SYMBOL, 60, [(now, 83000.)], alert)
        _, loaded = load_snapshots(self.path, SYMBOL, 60, now=now + 1)
        self.assertIsNone(loaded["last_reversal_preview"])

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



    def test_corrupt_primary_restores_valid_backup_without_waiting_an_hour(self):
        now = 2000000000
        observations = [(now - 59 * 60 + i * 60, 83000. + i)
                        for i in range(60)]
        alert = {"day": "2026-10-13", "count": 7,
                 "last_time": now - 30, "last_side": "롱",
                 "last_reversal_preview": now - 60}
        save_snapshots(self.path, SYMBOL, 60, observations, alert)
        self.assertTrue(os.path.exists(self.path + ".bak"))
        with open(self.path, "w", encoding="utf-8") as file:
            file.write('{"schema":1,"samples":[')
        recovered, recovered_alert = load_snapshots(
            self.path, SYMBOL, 60, now=now + 10)
        self.assertEqual(recovered, observations)
        self.assertEqual(recovered_alert, alert)

    def test_corrupt_backup_keeps_valid_primary(self):
        now = 2000000000
        observations = [(now - 120, 83000.), (now - 60, 83020.), (now, 83040.)]
        save_snapshots(self.path, SYMBOL, 60, observations, {})
        with open(self.path + ".bak", "w", encoding="utf-8") as file:
            file.write("no JSON")
        observed, _ = load_snapshots(self.path, SYMBOL, 60, now=now + 20)
        self.assertEqual(observed, observations)

    def test_both_replicas_stale_never_resurrect_old_candles(self):
        now = 2000000000
        observations = [(now - 600, 83300.), (now - 540, 83320.)]
        alert = {"day": "2026-10-13", "count": 4, "last_time": now - 600,
                 "last_side": "숏"}
        save_snapshots(self.path, SYMBOL, 60, observations, alert)
        observed, restored = load_snapshots(self.path, SYMBOL, 60, now=now)
        self.assertEqual(observed, [])
        self.assertEqual(restored["count"], 4)

    def test_crash_left_temp_file_does_not_affect_recovery(self):
        now = 2000000000
        observations = [(now - 60, 83300.), (now, 83320.)]
        save_snapshots(self.path, SYMBOL, 60, observations, {})
        with open(self.path + ".tmp", "w", encoding="utf-8") as file:
            file.write("bad")
        observed, _ = load_snapshots(self.path, SYMBOL, 60, now=now + 10)
        self.assertEqual(observed, observations)
        self.assertFalse(os.path.exists(self.path + ".tmp.tmp"))

    def test_gap_in_backup_cannot_generate_synthetic_samples(self):
        now = 2000000000
        observations = [(now - 3500, 83300.),
                        (now - 120, 83320.), (now - 60, 83330.), (now, 83340.)]
        save_snapshots(self.path, SYMBOL, 60, observations, {})
        with open(self.path, "w", encoding="utf-8") as file:
            file.write("broken")
        observed, _ = load_snapshots(self.path, SYMBOL, 60, now=now + 10)
        self.assertEqual(observed, observations[-3:])


if __name__ == "__main__":
    unittest.main()
