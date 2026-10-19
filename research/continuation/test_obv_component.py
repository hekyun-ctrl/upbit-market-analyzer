import unittest
import numpy as np
import pandas as pd
from research.continuation.obv_component import (signals,minute_positions,quality_months,monthly_path,select_rules,portfolio_curve,rule_grid)

class PublishedOBVTests(unittest.TestCase):
    def test_equal_close_adds_volume_per_source(self):
        self.assertTrue(signals(np.ones(5)*100,np.ones(5),2,3)[-1])
    def test_rule_count_matches_frozen_subset(self):
        self.assertEqual(len(rule_grid()),165)
    def test_causal_bar_signal_and_publication_buffer(self):
        t=np.arange(70)*60
        m=pd.DataFrame(dict(start=t,close=100+np.arange(70),volume=1.))
        p=minute_positions(m,10,2,3)
        self.assertEqual(p[30],0);self.assertEqual(p[31],1)
        m.loc[m.start>=40*60,'volume']=10000
        self.assertTrue(np.array_equal(p[:40],minute_positions(m,10,2,3)[:40]))
    def test_missing_minute_excludes_entire_month(self):
        start=int(pd.Timestamp('2023-01-01',tz='UTC').timestamp());end=int(pd.Timestamp('2023-04-01',tz='UTC').timestamp())
        t=np.arange(start,end+60,60);m=pd.DataFrame(dict(start=t,open=100.,volume=1.))
        r,s=quality_months(m);self.assertIn('2023-02',s)
        m=m[m.start!=int(pd.Timestamp('2023-02-20',tz='UTC').timestamp())].reset_index(drop=True)
        self.assertNotIn('2023-02',quality_months(m)[1])
    def test_trade_daily_accounting_and_funding_at_exit(self):
        t=np.arange(6)*60;p=np.array([100,100,110,105,103,100.]);d=np.array([0,1,1,0,1,0]);f=np.array([0,.1,.01,.02,0,.03])
        day,v,trades=monthly_path(t,p,d,f,0,5)
        self.assertEqual(len(trades),2)
        self.assertAlmostEqual(sum(x['net_log'] for x in trades),v[:,1].sum())
        self.assertAlmostEqual(trades[0]['adverse_funding_rate_sum'],.03)
        self.assertAlmostEqual(trades[1]['adverse_funding_rate_sum'],.03)
        self.assertLess(v[:,2].sum(),v[:,1].sum())
    def test_month_selection_ignores_future_performance(self):
        labels=[str(x)[:7] for x in pd.date_range('2023-01-01',periods=15,freq='MS')]
        a=np.zeros((3,15));a[1,:12]=1
        chosen,_=select_rules(a,labels,'2024-01',1)
        a[2,12:]=1e6
        self.assertEqual(chosen[0],1);self.assertEqual(select_rules(a,labels,'2024-01',1)[0][0],1)
    def test_selection_requires_quality_training_months(self):
        self.assertIsNone(select_rules(np.zeros((2,3)),['2023-01','2023-02','2023-03'],'2024-01',1)[0])
    def test_equal_initial_sleeves_are_not_daily_rebalanced(self):
        x=np.log(np.array([[2.,.5],[1.,1.]]));curve=portfolio_curve(x,np.array([0,1]))
        self.assertAlmostEqual(curve[0],1.5);self.assertAlmostEqual(curve[-1],1.)

if __name__=='__main__':unittest.main()
