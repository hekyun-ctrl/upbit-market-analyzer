"""Regression tests confirm audit observations, not strategy success."""
import os
import unittest
from pathlib import Path

import audit_external_ema_source as audit


@unittest.skipUnless(os.environ.get("LBANK_EMA_AUDIT_SOURCES"),
                     "Opt-in forensic test: recover hash-pinned external sources first")
class SourceAuditTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.sources = Path(os.environ["LBANK_EMA_AUDIT_SOURCES"])
        audit.recover_sources(cls.sources)
        cls.strategy, cls.generator, cls.tree = audit.load_inspected_source(cls.sources / "ema_pullback_momentum.py")
        cls.probes = {p["id"]: p for p in audit.direct_probes(cls.strategy, cls.tree)}

    def test_hash_guard(self):
        with self.assertRaises(ValueError):
            audit.load_inspected_source(Path(__file__))

    def test_buy_prebreak(self):
        self.assertTrue(self.probes["buy_prebreak_open"]["entry_on_pretrigger_side"])

    def test_sell_prebreak(self):
        self.assertTrue(self.probes["sell_prebreak_open"]["entry_on_pretrigger_side"])

    def test_gap_buy(self):
        self.assertTrue(self.probes["buy_gap_untraded_fill"]["entry_outside_range"])

    def test_gap_sell(self):
        self.assertTrue(self.probes["sell_gap_untraded_fill"]["entry_outside_range"])

    def test_nonpositive_initial_risk_both_sides(self):
        for side in ("buy", "sell"):
            self.assertLessEqual(self.probes[f"{side}_negative_initial_risk"]["initial_risk"], 0)

    def test_intrabar_stop_ignored(self):
        self.assertTrue(self.probes["intrabar_long_stop_ignored"]["position_still_open"])

    def test_intrabar_target_ignored(self):
        self.assertTrue(self.probes["intrabar_long_target_ignored"]["position_still_open"])

    def test_gap_stop_outside_range(self):
        p = self.probes["gap_stop_fill_above_entire_range"]
        self.assertGreater(p["exit"], p["range_high"])

    def test_retrigger_without_new_setup(self):
        self.assertTrue(self.probes["consumed_signal_reused"]["second_trigger_without_new_setup"])

    def test_adx_has_no_finite_values(self):
        self.assertEqual(self.probes["datetime_adx_alignment"]["finite_assigned_rows"], 0)

    def test_unused_centered_swing_not_active_leak(self):
        p = self.probes["centered_swing_future_dependency"]
        self.assertTrue(p["past_value_changes"])
        self.assertEqual(p["swing_field_reads_in_entry_exit_methods"], 0)
        self.assertFalse(p["demonstrated_active_signal_leak"])

    def test_gross_only_flat_trade(self):
        self.assertEqual(self.probes["flat_trade_has_no_cost"]["source_pnl"], 0)

    def test_generator_datetime_is_frozen(self):
        self.assertEqual(audit.FrozenDatetime.now(), audit.NOW)


if __name__ == "__main__":
    unittest.main()
