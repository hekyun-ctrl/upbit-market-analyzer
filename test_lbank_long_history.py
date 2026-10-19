"""Causal minute execution and external-data research boundaries."""
import copy
import unittest
import numpy as np
import pandas as pd
from research.long_history.evaluate import (aggregate_minutes,feature_frame,build_events,evaluate_events,
                                           select_portfolio,unit_net,block_ci,train_meta)


def minutes(count=300,base=80000.,start=1704067200):
    i=np.arange(count);p=base+i*.1
    return pd.DataFrame(dict(start=start+i*60,open=p,high=p+1,low=p-1,close=p+.2,volume=np.ones(count)*10))


class LongHistoryTests(unittest.TestCase):
    def test_aggregation_does_not_fill_a_missing_minute(self):
        df=minutes(15);self.assertEqual(len(aggregate_minutes(df)),3)
        broken=df.drop(index=6);agg=aggregate_minutes(broken)
        self.assertEqual(len(agg),2);self.assertNotIn(df['start'].iloc[5],agg['start'].tolist())

    def test_features_do_not_change_when_only_future_prices_change(self):
        m=minutes(1400);f=aggregate_minutes(m);original=feature_frame(f.iloc[:260].copy())
        future=f.copy();future.loc[260:,['open','high','low','close']]*=2
        replay=feature_frame(future).iloc[:260]
        pd.testing.assert_frame_equal(original,replay)

    def test_hour_direction_never_uses_a_forming_hour(self):
        five=aggregate_minutes(minutes(240));f=feature_frame(five)
        self.assertEqual(f['hour_dir'].iloc[34],0)
        self.assertEqual(f['hour_dir'].iloc[35],1)
        changed=five.copy();changed.loc[36:,['open','high','low','close']]*=.5
        self.assertEqual(feature_frame(changed)['hour_dir'].iloc[35],1)

    def event(self,d=1):
        return dict(side=d,entry_at=1704067200,atr=30.,anchor=80000.-d*50,
                    target=80000.+d*300,vwap=80000.,close=80000.,family=0)

    def funding(self):return pd.DataFrame(dict(calc_time=[],last_funding_rate=[]))

    def test_post_signal_original_minute_entry_and_same_minute_conflict_stop_first(self):
        for d in (1,-1):
            m=minutes(130);m.loc[0,'high']=80400.;m.loc[0,'low']=79600.
            result,reject=evaluate_events(pd.DataFrame([self.event(d)]),m,self.funding())
            self.assertEqual(reject,{})
            self.assertEqual(result.iloc[0]['reason'],'ambiguous_stop_first')
            self.assertAlmostEqual(result.iloc[0]['net_r'],-1.)
            self.assertEqual(result.iloc[0]['mfe_lower_price'],0.)

    def test_minute_gap_censors_without_interpolating_or_inventing_a_win(self):
        m=minutes(130).drop(index=4)
        result,_=evaluate_events(pd.DataFrame([self.event()]),m,self.funding())
        self.assertEqual(result.iloc[0]['reason'],'censored_gap')
        self.assertTrue(np.isnan(result.iloc[0]['net_r']))

    def test_archived_funding_is_conservative_and_does_not_invent_receipts(self):
        m=minutes(130,start=1704067200)
        rate=pd.DataFrame(dict(calc_time=[(1704067200+3600)*1000],last_funding_rate=[.0001]))
        for d,positive in ((1,True),(-1,False)):
            result,_=evaluate_events(pd.DataFrame([self.event(d)]),m,rate)
            cost=result.iloc[0]['funding_cost_unit']
            self.assertEqual(cost>0,positive)
            self.assertLess(result.iloc[0]['stress_r'],result.iloc[0]['net_r'])

    def test_missing_entry_minute_is_rejected_not_replaced_with_a_later_cheaper_quote(self):
        m=minutes(130).iloc[1:]
        result,rejections=evaluate_events(pd.DataFrame([self.event()]),m,self.funding())
        self.assertTrue(result.empty);self.assertEqual(rejections,{'entry_minute_missing':1})

    def test_single_position_and_opposite_conflicts_are_not_independent_wins(self):
        rows=pd.DataFrame([dict(entry_at=10000,exit_at=11000,family=0,side=1),
                           dict(entry_at=10000,exit_at=11000,family=1,side=-1),
                           dict(entry_at=11000,exit_at=12000,family=0,side=1),
                           dict(entry_at=11500,exit_at=12500,family=0,side=1)])
        self.assertEqual(select_portfolio(rows).index.tolist(),[2])
        scores=pd.Series([.3,.4,.3,.5])
        self.assertEqual(select_portfolio(rows,scores,.1).index.tolist(),[2])

    def test_stress_and_bootstrap_do_not_turn_negative_results_positive(self):
        for d in (1,-1):self.assertLess(unit_net(d,80000,80000+d*100,stress=2),unit_net(d,80000,80000+d*100))
        ci=block_ci(np.full(30,-.2));self.assertLess(ci['upper95'],0)


if __name__=='__main__':unittest.main()
