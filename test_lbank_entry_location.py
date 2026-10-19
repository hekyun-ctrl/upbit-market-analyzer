"""Pending price-location and causal protection policy tests."""
import unittest
import numpy as np
from research.long_history.entry_location import fill_and_follow


class EntryLocationTests(unittest.TestCase):
    def market(self,count=150):
        t=1704067200+np.arange(count)*60
        return (t,np.full(count,80010.),np.full(count,80020.),np.full(count,80005.),np.full(count,80010.))

    def plan(self):
        return dict(family=0,side=1,kind='limit',plan_at=1704067200,level=80000.,stop=79940.,target=80300.,atr=30.,unit_net_risk=188.)

    def test_touch_without_penetration_is_not_a_claimed_fill(self):
        m=self.market();m[3][0]=80000.
        row,reason,until=fill_and_follow(self.plan(),m,np.array([]),np.array([]))
        self.assertIsNone(row);self.assertEqual(reason,'unfilled_expiry')

    def test_entry_minute_target_cannot_be_credited_before_unknown_entry_order(self):
        m=self.market();m[3][0]=79995.;m[2][0]=80400.
        row,_,_=fill_and_follow(self.plan(),m,np.array([]),np.array([]))
        self.assertEqual(row['reason'],'time_exit');self.assertFalse(row['target_hit'])

    def test_entry_minute_stop_wins_even_if_target_also_touched(self):
        m=self.market();m[3][0]=79900.;m[2][0]=80400.
        row,_,_=fill_and_follow(self.plan(),m,np.array([]),np.array([]))
        self.assertEqual(row['reason'],'stop');self.assertLess(row['net_r'],0)

    def test_new_guard_is_not_used_retroactively_inside_its_creation_minute(self):
        m=self.market();m[3][0]=79995.;m[2][0]=80400.;m[4][0]=80270.
        # Entry-minute low was below the new guard before its creation.
        m[1][1]=80260.;m[2][1]=80280.;m[3][1]=80150.;m[4][1]=80160.
        row,_,_=fill_and_follow(self.plan(),m,np.array([]),np.array([]))
        self.assertEqual(row['reason'],'profit_guard_exit');self.assertGreater(row['net_r'],0)
        self.assertEqual(row['exit_at'],1704067200+120)

    def test_limit_gap_is_not_assumed_to_fill_at_a_better_price(self):
        m=self.market();m[1][0]=79800.;m[2][0]=79810.;m[3][0]=79790.
        row,_,_=fill_and_follow(self.plan(),m,np.array([]),np.array([]))
        self.assertEqual(row['entry'],80000.);self.assertEqual(row['exit'],79800.)
        self.assertEqual(row['reason'],'gap_stop')

    def test_stop_market_gap_chase_does_not_become_an_immediate_winner(self):
        p=self.plan();p['kind']='stop_market';m=self.market()
        m[1][0]=80400.;m[2][0]=80410.;m[3][0]=80390.
        row,_,_=fill_and_follow(p,m,np.array([]),np.array([]))
        self.assertEqual(row['reason'],'gap_chase_exit');self.assertLess(row['net_r'],0)

    def test_pending_data_gap_does_not_fill_from_a_later_favorable_bar(self):
        m=list(self.market());m[0][1]+=60
        row,why,_=fill_and_follow(self.plan(),tuple(m),np.array([]),np.array([]))
        self.assertIsNone(row);self.assertEqual(why,'pending_data_gap')


if __name__=='__main__':unittest.main()
