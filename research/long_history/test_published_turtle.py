import unittest
import numpy as np
import pandas as pd
from research.long_history.published_turtle import daily_context,replay

class TurtleAuditTests(unittest.TestCase):
    def setup_context(self):
        return pd.DataFrame([dict(day=0,N=10.,upper=100.,lower=50.,exitlong=40.,exitshort=150.)]).set_index('day')
    def fund(self):return pd.DataFrame(dict(calc_time=[],last_funding_rate=[]))
    def test_future_day_not_in_prior_channel(self):
        t=np.arange(57*1440)*60
        m=pd.DataFrame(dict(start=t,open=80.,high=90.,low=70.,close=80.))
        m.loc[m.start>=56*86400,'high']=999.
        c=daily_context(m)
        self.assertAlmostEqual(c.loc[56*86400,'upper'],90.1)
        self.assertAlmostEqual(c.loc[57*86400,'upper'],999.1)
    def test_incomplete_day_breaks_55_day_eligibility(self):
        t=np.arange(57*1440)*60
        m=pd.DataFrame(dict(start=t,open=80.,high=90.,low=70.,close=80.));m=m[m.start!=55*86400]
        self.assertTrue(np.isnan(daily_context(m).loc[56*86400,'upper']))
    def test_entry_then_large_reversal_is_loss_not_free_exit(self):
        m=pd.DataFrame([dict(start=0,open=99.,high=110.,low=60.,close=70.)])
        eq,tr,fi,opened=replay(m,self.fund(),self.setup_context(),'HL')
        self.assertLess(tr.net.sum(),0)
        self.assertTrue((fi.price[fi.event=='exit']<fi.price[fi.event=='entry'].iloc[0]).any())
        self.assertIsNone(opened)
    def test_add_trigger_uses_slipped_previous_fill(self):
        m=pd.DataFrame([dict(start=0,open=99.,high=119.,low=98.,close=118.)])
        eq,tr,fi,opened=replay(m,self.fund(),self.setup_context(),'LH')
        self.assertEqual(len(fi),4)
        prices=fi.price.to_numpy()
        self.assertTrue(np.allclose(prices[1:],(prices[:-1]+5)*1.0002))
    def test_doubled_cost_reduces_same_path_result(self):
        m=pd.DataFrame([dict(start=0,open=99.,high=110.,low=60.,close=70.)])
        a=replay(m,self.fund(),self.setup_context(),'HL',1.)[1].net.sum()
        b=replay(m,self.fund(),self.setup_context(),'HL',2.)[1].net.sum()
        self.assertLess(b,a)
    def test_gap_blocks_reentry_for_rest_of_incomplete_day(self):
        m=pd.DataFrame([dict(start=0,open=101.,high=102.,low=100.,close=101.),dict(start=120,open=101.,high=102.,low=100.,close=101.),dict(start=180,open=101.,high=102.,low=100.,close=101.)])
        eq,tr,fi,opened=replay(m,self.fund(),self.setup_context())
        self.assertEqual(len(fi),2)
        self.assertIsNone(opened)
        self.assertTrue(tr.data_gap.iloc[0])
    def test_funding_paid_only_after_position_exists(self):
        m=pd.DataFrame([dict(start=0,open=101.,high=102.,low=100.,close=101.),dict(start=60,open=101.,high=102.,low=100.,close=101.)])
        f=pd.DataFrame(dict(calc_time=[0,60000],last_funding_rate=[.01,.01]))
        opened=replay(m,f,self.setup_context())[3]
        self.assertAlmostEqual(opened['funding'],.0025/10*.01*101.)

    def test_account_reconciles_closed_and_open_pnl(self):
        m=pd.DataFrame([dict(start=0,open=99.,high=120.,low=98.,close=118.),dict(start=60,open=118.,high=119.,low=60.,close=70.)])
        eq,tr,fi,opened=replay(m,self.fund(),self.setup_context(),'LH')
        self.assertAlmostEqual(eq.equity.iloc[-1]-1,tr.net.sum()+(opened['marked_net'] if opened else 0.))
        exposure=fi.event.map({'entry':1,'add':1,'exit':-1}).cumsum()
        self.assertLessEqual(exposure.max(),4)
        self.assertGreaterEqual(exposure.min(),0)
    def test_opening_gap_does_not_fill_at_stale_stop(self):
        m=pd.DataFrame([dict(start=0,open=101.,high=102.,low=100.,close=101.),dict(start=60,open=60.,high=65.,low=55.,close=60.)])
        eq,tr,fi,opened=replay(m,self.fund(),self.setup_context())
        self.assertAlmostEqual(fi.loc[fi.event=='exit','price'].iloc[0],60*.9998)
    def test_favorable_funding_not_counted_as_income(self):
        m=pd.DataFrame([dict(start=0,open=101.,high=102.,low=100.,close=101.),dict(start=60,open=101.,high=102.,low=100.,close=101.)])
        f=pd.DataFrame(dict(calc_time=[60000],last_funding_rate=[-.01]))
        self.assertEqual(replay(m,f,self.setup_context())[3]['funding'],0.)

if __name__=='__main__':unittest.main()
