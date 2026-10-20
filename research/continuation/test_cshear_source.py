import os
from pathlib import Path
import unittest
from decimal import Decimal
from audit_cshear_source import fixture_probes,unit_costs

class SourceContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.p=fixture_probes(Path(os.environ['CSHEAR_AUDIT_SOURCE']))

    def test_selected_later_candle_changes_past_query(self):
        x=self.p['nearest_lookup'];self.assertTrue(x['future_label_selected'])
        self.assertEqual((x['prefix_label'],x['full_label']),(0,14400))
        self.assertEqual((x['prefix_close'],x['full_close']),(100,120))

    def test_tie_accessors_disagree_without_inventing_price(self):
        self.assertEqual(self.p['tie_lookup'],dict(at_label=0,index_label=14400))

    def test_later_funding_changes_earlier_query(self):
        x=self.p['funding_future_append']
        self.assertAlmostEqual(x['prefix'],.0001);self.assertAlmostEqual(x['full'],.0005)

    def test_three_funding_values_not_order_independent_mean(self):
        x=self.p['funding_input_order']
        self.assertAlmostEqual(x['chronological'],.000525)
        self.assertAlmostEqual(x['reversed'],.000325)
        self.assertAlmostEqual(x['arithmetic_mean'],.0004)

    def test_source_fixed_short_recovers_opposite_sides_after_future_changes(self):
        x=self.p['funding_direction_recovery']
        self.assertEqual([r['source_expected_side'] for r in x],[-1,-1])
        for field in ('inferred_side_exit','inferred_side_tp'):
            self.assertEqual([r[field] for r in x],[1,-1])

    def test_small_return_fallback_flips_explicit_long(self):
        self.assertEqual([x['inferred'] for x in self.p['small_return_direction_recovery']],[-1,1])

    def test_sample_end_becomes_profitable_early_time_exit(self):
        pnl,hours,reason=self.p['sample_end_source_exit']
        self.assertAlmostEqual(pnl,.1994);self.assertEqual((hours,reason),(4,'time_exit'))

    def test_gap_compresses_timestamp_duration(self):
        x=self.p['gap_time_compression']
        self.assertEqual(x['actual_timestamp_span_hours'],8)
        self.assertEqual(x['source_exit'][1],4)
        self.assertAlmostEqual(x['source_forward_return'],.2)

    def test_worse_open_not_applied_to_source_stop(self):
        self.assertAlmostEqual(self.p['stop_gap_source_exit'][0],-.0206)
        self.assertGreater(self.p['stop_gap_source_exit'][0],-.10)

    def test_both_touch_stop_first_positive_control(self):
        self.assertEqual(self.p['both_touch_stop_first_control'][2],'stop_loss')
        self.assertAlmostEqual(self.p['both_touch_stop_first_control'][0],-.0206)

    def test_identical_OHLC_trailing_paths_differ(self):
        x=self.p['tp_trail_same_OHLC_paths']
        self.assertAlmostEqual(x['high_then_low'][0],.0944)
        self.assertAlmostEqual(x['low_then_high'][0],.0974)
        self.assertNotEqual(x['high_then_low'][1],x['low_then_high'][1])
        self.assertAlmostEqual(x['source'][0],x['high_then_low'][0])

    def test_current_source_rsi24_sma_fixture(self):
        self.assertAlmostEqual(self.p['source_rsi24_uptrend_fixture_pnl'],.0094)

    def test_double_full_unit_costs_without_changing_reference_move(self):
        rows=unit_costs()['rows']
        for d in (1,-1):
            for move in ('.05','-.02'):
                x=[r for r in rows if r['side']==d and Decimal(r['price_move_fraction'])==Decimal(move)]
                self.assertEqual(len(x),2)
                self.assertLess(Decimal(x[1]['net_cash']),Decimal(x[0]['net_cash']))

if __name__=='__main__':unittest.main()
