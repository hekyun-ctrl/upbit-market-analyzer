"""Forensic regression tests reproduce counterexamples, not strategy qualification."""
import json
import os
from pathlib import Path
import shutil
import tempfile
import unittest

from audit_lightgbm_source_contract import load_kernels, load_verified, run_probes


class SourceContractAuditTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.source_dir = Path(os.environ["ML_AUDIT_SOURCE_DIR"])
        cls.result = run_probes(cls.source_dir)

    def test_all_pinned_source_hashes_verified(self):
        self.assertEqual(self.result["source_files_hash_verified"], 12)

    def test_mutated_source_rejected(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder) / "source"
            shutil.copytree(self.source_dir, root)
            p = root / "src/backtest.py"
            p.write_bytes(p.read_bytes() + b"\n# mutation\n")
            with self.assertRaisesRegex(ValueError, "Pinned source mismatch"):
                load_verified(root)

    def test_only_reviewed_kernels_available(self):
        kernels, spans, *_ = load_kernels(self.source_dir)
        self.assertEqual(len(spans), 19)
        for name in ["lgb", "os", "main", "train_and_predict", "discover_snapshots"]:
            self.assertNotIn(name, kernels)
        with self.assertRaisesRegex(ValueError, "Only installed"):
            kernels["__builtins__"]["__import__"]("os")

    def test_entry_bar_stop_fee_events_disappear(self):
        row = self.result["entry_bar_stop_fee_and_ledger"]["cases"][0]
        self.assertAlmostEqual(row["gross_return"], -0.20)
        self.assertEqual(row["reported_Total_Trades"], 0)

    def test_same_signal_double_fee_has_zero_effect_on_first_bar_stop(self):
        a, b = self.result["entry_bar_stop_fee_and_ledger"]["cases"]
        self.assertEqual(a["modified_signal"], b["modified_signal"])
        self.assertEqual(a["net_array"], b["net_array"])
        self.assertEqual(a["gross_array"], a["net_array"])

    def test_entry_bar_stop_missing_from_closed_table(self):
        rows = self.result["entry_bar_stop_fee_and_ledger"]["cases"]
        self.assertTrue(all(row["closed_table_rows"] == 0 for row in rows))

    def test_normal_round_trip_counts_transitions_not_closed_trades(self):
        a = self.result["normal_round_trip_control"][0]
        self.assertEqual(a["reported_Total_Trades"], 2)
        self.assertEqual(a["closed_table_rows"], 1)

    def test_month_end_can_open_from_flat(self):
        case = self.result["month_end_new_entry"]
        self.assertTrue(case["violation"])
        self.assertEqual(case["new_month_first_held"], 1)

    def test_unknown_quantile_can_publish_live_exit(self):
        case = self.result["unknown_quantile_live_exit"]
        self.assertFalse(case["valid"][1])
        self.assertTrue(case["exit_on_invalid_bar"])

    def test_stop_lock_and_level_entry_contract_disagree(self):
        case = self.result["stop_lock_vs_live_level_flags"]
        self.assertTrue(case["next_bar_live_entry_flag_while_model_locked"])
        self.assertFalse(case["actual_Freqtrade_order_or_fill_tested"])

    def test_documented_intrabar_stop_approximation_reproduced(self):
        case = self.result["intrabar_rebound_approximation"]
        self.assertTrue(case["low_crosses_nominal_stop"])
        self.assertEqual(sum(case["source_stop_flags"]), 0)

    def test_held_price_gap_not_in_open_close_product(self):
        case = self.result["held_gap_return_omission"]
        self.assertEqual(case["source_open_close_product_return"], 0)
        self.assertAlmostEqual(case["continuous_held_entry_to_exit_price_return"], -0.1)

    def test_rolling_quantile_prefix_control(self):
        self.assertEqual(self.result["causal_controls"]["rolling_quantile_past_changes"], 0)

    def test_feature_prefix_control(self):
        self.assertEqual(self.result["causal_controls"]["feature_past_cells_changed_at_1e_12_tolerance"], 0)

    def test_one_bar_shift_control(self):
        self.assertEqual(self.result["causal_controls"]["one_bar_shift"], [0, 0, 1, 1])

    def test_missing_public_artifacts_and_unassessed_gates_explicit(self):
        self.assertFalse(any(self.result["artifacts"].values()))
        for key in ["profit_factor", "net_expectancy_95lower", "all_cost_double_profitability", "alerts_per_day", "TP1_Wilson_lower"]:
            self.assertIsNone(self.result[key])
        self.assertFalse(self.result["market_profitability_backtest_performed"])


if __name__ == "__main__":
    unittest.main()
