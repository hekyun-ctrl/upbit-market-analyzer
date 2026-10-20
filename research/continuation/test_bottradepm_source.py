import os,tempfile,unittest
from audit_bottradepm_source import fixtures


class ContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp=tempfile.TemporaryDirectory()
        cls.r,cls.defs=fixtures(os.environ['BOTTRADEPM_SOURCE'],cls.tmp.name)
    @classmethod
    def tearDownClass(cls):cls.tmp.cleanup()
    def test_21_reviewed_definitions(self):self.assertEqual(len(self.defs),21)
    def test_short_entry_slips_favorably(self):
        self.assertGreater(self.r['short_slippage']['short_entry'],100.)
    def test_short_exit_slips_favorably(self):
        self.assertLess(self.r['short_slippage']['short_exit'],100.)
    def test_partial_events_not_closed_trade_hit_rate(self):
        r=self.r['partial_event_statistics']
        self.assertEqual(r['source_report']['win_rate_pct'],50.)
        self.assertEqual(r['economic_closed_campaigns'],1);self.assertEqual(r['economic_closed_winners'],0)
    def test_double_entry_fee_cash_gap(self):
        r=self.r['partial_event_statistics']
        self.assertAlmostEqual(r['source_minus_correct_cash'],-r['extra_entry_fee'])
    def test_ledger_cash_sums_source_equity(self):
        r=self.r['partial_event_statistics']
        self.assertAlmostEqual(sum(x['realized_pnl'] for x in r['events']),r['source_cash_net'])
    def test_remaining_quantity_closes(self):
        r=self.r['partial_event_statistics'];q=r['events']
        self.assertAlmostEqual(q[0]['qty'],q[1]['qty']+q[2]['qty'])
    def test_tail_is_forced_closed_not_censored(self):
        self.assertEqual(self.r['tail_forced_close']['forced_closes'],1)
    def test_tail_equity_curve_not_updated(self):
        self.assertGreater(self.r['tail_equity_terminal'],self.r['tail_forced_close']['ending_equity'])
    def test_no_funding_interface(self):self.assertTrue(self.r['funding_interface_absent'])


if __name__=='__main__':unittest.main()
