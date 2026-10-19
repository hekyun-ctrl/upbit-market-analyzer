"""Opt-in source regression probes; tests confirm audit observations."""
import os
import unittest
from pathlib import Path

import audit_supply_demand_source as audit


@unittest.skipUnless(os.environ.get("LBANK_SUPPLY_DEMAND_SOURCES"), "Recover pinned source files and opt in")
class SourceCausalityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.sources = Path(os.environ["LBANK_SUPPLY_DEMAND_SOURCES"])
        cls.ns = audit.load_source(cls.sources)
        cls.p = audit.probes(cls.ns)

    def test_integrity_rejects_changed_source(self):
        with self.assertRaises((FileNotFoundError, ValueError)):
            audit.load_source(Path(__file__).parent)

    def test_config_side_effect_is_not_loaded(self):
        self.assertNotIn("OUTPUT_DIR", self.ns)

    def test_future_liquidity_changes_target(self):
        p = self.p["future_target"]
        self.assertNotEqual(p["fallback"], p["with_future_level"])

    def test_future_low_changes_past_sweep(self):
        p = self.p["future_sweep"]
        self.assertTrue(p["historical_prefix_unchanged"])
        self.assertFalse(p["before"])
        self.assertTrue(p["after_future_low_change"])

    def test_unconfirmed_centered_swing(self):
        p = self.p["unconfirmed_swing"]
        self.assertTrue(p["swing25_in_full"])
        self.assertFalse(p["swing25_in_prefix_ending26"])

    def test_trade_before_formation_candle_closes(self):
        self.assertTrue(any(t["entry_bar"] == t["exit_bar"] == t["impulse_bar"]
                            for t in self.p["forming_bar_round_trip"]))

    def test_gap_stop_is_outside_bar_range(self):
        self.assertGreater(self.p["gap_stop"]["exit_price"], self.p["gap_stop"]["bar_high"])

    def test_close_suppresses_earlier_stop_out(self):
        p = self.p["closing_freshness"]
        self.assertEqual(p["unchanged_OHL_close100_trades"], 1)
        self.assertEqual(p["same_OHL_close98_trades"], 0)

    def test_stop_first_and_unknown_end_controls(self):
        self.assertEqual(self.p["both_touched_stop_first"]["result"], "loss")
        self.assertEqual(self.p["unresolved_data_end"]["result"], "open")
        self.assertFalse(self.p["unresolved_data_end"]["completed_trade"])

    def test_fee_arithmetic_control(self):
        p = self.p["fee_arithmetic_control"]
        self.assertAlmostEqual(p["recorded_fee"], p["unrounded_price_fee"], places=2)
        self.assertLess(p["double_fee_R_same_fills"], p["base_R_from_same_fills"])


if __name__ == "__main__":
    unittest.main()
