"""Causal research exit/flow boundaries, not profitability tests."""
import unittest
import numpy as np
import pandas as pd
from research.long_history.evaluate import unit_net
from research.long_history.exit_intervention import target_for_net,flow_frame,follow
from research.long_history.independent_structures import events_for_segment


class ExitInterventionTests(unittest.TestCase):
    def test_net_target_solver_both_directions(self):
        for d in (1,-1):
            for value in (0.,100.,400.):
                target=target_for_net(d,80000.,value)
                self.assertAlmostEqual(unit_net(d,80000.,target),value,places=8)

    def test_flow_features_do_not_use_future_minutes(self):
        n=150;f=pd.DataFrame(dict(start=np.arange(n)*60,open=np.full(n,80000.),
            close=80000.+np.arange(n),volume=np.full(n,10.),buy_volume=np.full(n,7.),
            quote_volume=np.full(n,800000.),trades=np.full(n,100.)))
        expected=flow_frame(f.iloc[:100].copy());changed=f.copy()
        changed.loc[100:,['buy_volume','quote_volume','close']]=[1.,100.,1.]
        pd.testing.assert_frame_equal(expected,flow_frame(changed).iloc[:100])

    def test_invalid_aggressive_flow_is_rejected(self):
        f=pd.DataFrame(dict(start=[0],volume=[1.],buy_volume=[2.],trades=[1.]))
        with self.assertRaises(ValueError):flow_frame(f)

    def test_new_guard_never_retroactively_uses_earlier_wick(self):
        t=1704067200;n=130;starts=t+np.arange(n)*60
        op=np.full(n,80400.);hi=np.full(n,80401.);lo=np.full(n,80350.);cl=np.full(n,80400.)
        op[0]=80000.;lo[0]=79990.;op[2]=80100.;lo[2]=80090.;cl[2]=80100.
        e=dict(side=1,entry_at=t,entry=80000.,stop=79700.,target=82000.,
               unit_net_risk=-unit_net(1,80000.,79700.),atr=20.)
        r=follow(e,(starts,op,hi,lo,cl),np.array([],dtype=int),np.array([]),1)
        self.assertEqual(r['reason'],'guard_gap');self.assertEqual(r['exit_at'],t+180)
        self.assertEqual(r['exit'],80100.)

    def test_independent_signals_are_unchanged_by_future_prices(self):
        n=1200;i=np.arange(n);close=80000.+.5*i
        f=pd.DataFrame(dict(start=1704067200+i*60,open=close-.1,high=close+.1,
            low=close-.2,close=close,volume=np.full(n,10.),buy_volume=np.full(n,7.)))
        expected=events_for_segment(f.iloc[:900].copy());self.assertGreater(len(expected),0)
        changed=f.copy();changed.loc[900:,['open','high','low','close']]*=2
        actual=[e for e in events_for_segment(changed) if e['signal_at']<=int(f.start.iloc[899]+60)]
        pd.testing.assert_frame_equal(pd.DataFrame(expected),pd.DataFrame(actual))
        self.assertTrue(all(e['entry_at']==e['signal_at']+60 for e in actual))


if __name__=='__main__':unittest.main()
