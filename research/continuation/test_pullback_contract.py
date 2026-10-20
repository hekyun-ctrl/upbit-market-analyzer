"""Re-execute the original offline broker class, never a live broker."""
import json,unittest
from pathlib import Path
from audit_pullback_contract import kernels,broker_case

REPO='/workspace/scratch/ba9af914844b/pullback-source-1445'
# Set PULLBACK_SOURCE_REPO when replaying in a clean environment.
import os
REPO=os.environ.get('PULLBACK_SOURCE_REPO',REPO)

class ContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):cls.ns,cls.sp,cls.sample,_,_=kernels(REPO)
    def case(self,name,**kw):return broker_case(self.ns,name,**kw)
    def test_entrybar_unprotected(self):
        c=self.case('entrybar',entrybar=True);self.assertEqual(c['final_position'],1);self.assertEqual(c['closed'],[])
        self.assertLess(c['bars'][1][2],c['states'][1]['stop'])
    def test_long_double_exit(self):
        c=self.case('dual',dual=True);self.assertEqual(c['final_position'],-1);self.assertIsNone(c['states'][-1]['stop'])
        self.assertEqual(len([x for x in c['orders'] if x['status']=='Completed']),3)
    def test_short_double_exit(self):self.assertEqual(self.case('shortdual',direction=-1,dual=True)['final_position'],1)
    def test_stop_control(self):
        c=self.case('sl',stop=True);self.assertEqual(c['final_position'],0);self.assertAlmostEqual(c['closed'][0]['net'],-2.1188)
    def test_target_control(self):
        c=self.case('tp',take=True);self.assertEqual(c['final_position'],0);self.assertAlmostEqual(c['closed'][0]['net'],3.8776)
    def test_stop_gap_control(self):self.assertAlmostEqual(self.case('gap',gap=True)['closed'][0]['net'],-5.117)
    def test_margin_pending_not_cleared(self):
        c=self.case('margin',cash=50);self.assertTrue(c['states'][-1]['pending_entry']);self.assertEqual(c['states'][-1]['entry_refs'],1)
    def test_gap_reanchoring(self):self.assertEqual(self.case('entrygap',entrygap=True)['states'][-1]['stop'],108)
    def test_commission_doubling(self):
        c=self.case('twice',stop=True,commission=.0012);self.assertAlmostEqual(c['closed'][0]['net'],-2.2376)
    def test_original_analyzer_zero_quantity(self):
        c=self.case('tp',take=True);self.assertEqual(c['original_analyzer'][0]['size'],0);self.assertEqual(c['original_analyzer'][0]['exit_price'],100)
    def test_empty_summary_nonflat_equity(self):
        import pandas as pd
        s=self.ns['compute_summary'](pd.DataFrame(),pd.DataFrame({'equity':[1000,900]}),1000)
        self.assertEqual(s['max_drawdown_pct'],0);self.assertEqual(s['net_profit'],0)
    def test_source_rsi_warmup(self):
        import pandas as pd
        self.assertEqual(self.ns['rsi'](pd.Series([100.,99.]),14).tolist(),[100.,100.])
    def test_source_parameters(self):
        self.assertEqual(self.sample['backtest']['symbol'],'ETHUSDT');self.assertEqual(self.sp.max_pb,3);self.assertFalse(self.sp.use_chop);self.assertEqual(self.sp.rr,2)
    def test_loader_duplicate_not_rejected(self):
        import tempfile,pandas as pd
        with tempfile.TemporaryDirectory(prefix='pullback-duplicate-') as folder:
            p=Path(folder)/'fixture.csv'
            pd.DataFrame(dict(datetime=['2024-01-01T00:00:00Z']*2,open=[100]*2,high=[101]*2,low=[99]*2,close=[100]*2,volume=[1]*2)).to_csv(p,index=False)
            self.assertTrue(self.ns['load_ohlcv'](p).index.has_duplicates)
    def test_loader_gap_not_rejected(self):
        import tempfile,pandas as pd
        with tempfile.TemporaryDirectory(prefix='pullback-gap-') as folder:
            p=Path(folder)/'fixture.csv'
            pd.DataFrame(dict(datetime=['2024-01-01T00:00:00Z','2024-01-01T02:00:00Z'],open=[100]*2,high=[101]*2,low=[99]*2,close=[100]*2,volume=[1]*2)).to_csv(p,index=False)
            frame=self.ns['load_ohlcv'](p);self.assertEqual((frame.index[1]-frame.index[0]).total_seconds(),7200)

if __name__=='__main__':unittest.main()
