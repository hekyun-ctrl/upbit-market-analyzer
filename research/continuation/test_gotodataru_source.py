import os
import unittest
from audit_gotodataru_source import fixture_results


class SourceContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.r, cls.defs = fixture_results(os.environ['GOTODATARU_SOURCE'])

    def test_funding_cashflow_absent(self):
        self.assertTrue(self.r['funding_rate_sign_changed_pnl']['equal'])

    def test_source_end_closes_instead_of_censoring(self):
        self.assertEqual(self.r['end_truncation']['returned_bars'],2)
        self.assertEqual(self.r['end_truncation']['reason'],'hold')

    def test_gap_not_censored(self):
        self.assertEqual(self.r['gap_index_time']['elapsed_seconds'],171900.)
        self.assertEqual(self.r['gap_index_time']['returned_bars'],1)

    def test_stop_gap_fills_better_than_available_open(self):
        self.assertGreater(self.r['stop_gap']['stop_fill'],90.)

    def test_limit_target_no_adverse_exit_slip(self):
        r=self.r['target_exit_slippage'];self.assertEqual(r['source_exit'],r['source_target'])

    def test_actual_notionals_fee_difference(self):
        self.assertAlmostEqual(self.r['fee_notionals']['source_net_fraction'],.0992)
        self.assertEqual(self.r['fee_notionals']['actual_notionals_net'],'0.09916')

    def test_cost_stress_constructor_ignored(self):
        self.assertTrue(self.r['cost_override']['identical'])
        self.assertGreater(len(self.r['cost_override']['default_pnls']),0)

    def test_percent_label_factor_100(self):
        r=self.r['percent_units'];self.assertAlmostEqual(r['economic_noncompounded_percent'],r['source_total_return_pct_label']*100)

    def test_both_touch_correctly_stop_first(self):
        self.assertEqual(self.r['both_touch']['exit_reason'],'sl')

    def test_next_open_safe_control(self):
        r=self.r['safe_controls'];self.assertEqual(r['next_open_entry_time'],r['expected'])

    def test_signal_prefix_safe_control(self):
        r=self.r['safe_controls'];self.assertEqual(r['funding_mr_past_signals_changed'],0)
        self.assertGreater(r['past_signal_count'],0)


if __name__=='__main__':
    unittest.main()
