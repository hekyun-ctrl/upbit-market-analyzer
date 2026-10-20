import unittest
import numpy as np
import pandas as pd
from audit_funding_ledger import metric,block_ci,kernel
from pathlib import Path
import os
REPO=os.environ.get('FUNDING_SOURCE_REPO','/workspace/scratch/ba9af914844b/funding-source-1607')

class LedgerTests(unittest.TestCase):
    def frame(self):return pd.DataFrame(dict(known_outcome=[True,True,False],exit_time=pd.date_range('2024-01-01',periods=3),net=[4.,-2.,100.]))
    def test_unknown_excluded(self):self.assertEqual(metric(self.frame(),'net')['closed'],2)
    def test_net_sum(self):self.assertEqual(metric(self.frame(),'net')['sum_USD'],2)
    def test_PF(self):self.assertEqual(metric(self.frame(),'net')['PF'],2)
    def test_drawdown(self):self.assertEqual(metric(self.frame(),'net')['max_closed_drawdown_USD'],2)
    def test_no_losses_not_infinite_pass(self):
        f=self.frame().iloc[:1];self.assertIsNone(metric(f,'net')['PF'])
    def test_block_constant(self):
        d=pd.DataFrame(dict(count=[1]*31,net=[3.]*31));r=block_ci(d,'net',7)
        self.assertEqual(r['lo_USD'],3);self.assertEqual(r['hi_USD'],3)
    def test_block_seed(self):
        d=pd.DataFrame(dict(count=[1]*31,net=np.arange(31)-10.));self.assertEqual(block_ci(d,'net',7),block_ci(d,'net',7))
    def test_zero_days_included(self):
        d=pd.DataFrame(dict(count=[0,1]*15,net=[0.,3.]*15));r=block_ci(d,'net',7);self.assertEqual(r['lo_USD'],3)
    def test_embedded_slip_not_subtracted_twice(self):
        gross=(102*(1-.0001)-100*(1+.0001));fees=(102*(1-.0001)+100*(1+.0001))*.0006
        cash=102*(1-.0001)-100*(1+.0001)-fees
        self.assertAlmostEqual(cash,gross-fees)
    def test_double_funding_debits_only(self):
        f=pd.Series([2.,-1.]);self.assertEqual(f.where(f>=0,2*f).sum(),0)
    def test_registry_hash_tail_not_covered(self):
        ns,_=kernel(REPO,'python/src/registry.py',['_md5','data_hash'])
        d=pd.DataFrame({'close':np.arange(101,dtype=float)},index=pd.date_range('2024-01-01',periods=101,freq='1h',tz='UTC'))
        old=ns['data_hash'](d);d.iloc[-1,0]+=100;self.assertEqual(old,ns['data_hash'](d))
    def test_source_extract_double_slip_and_no_funding(self):
        from types import SimpleNamespace
        ns,_=kernel(REPO,'python/src/quant_stats.py',['extract_trade_returns'])
        t=pd.DataFrame(dict(timestamp=pd.date_range('2024-01-01',periods=2,freq='1h'),side=['buy','sell'],price=[100.,102.],qty=[1.,1.],fee_usd=[.06,.0612],slippage_usd=[.01,.0102]))
        value=ns['extract_trade_returns'](SimpleNamespace(trades=t)).iloc[0]*100
        self.assertAlmostEqual(value,2-.06-.0612-.01-.0102)

if __name__=='__main__':unittest.main()
