import copy
import unittest
import json
from pathlib import Path

import lbank_futures_monitor as monitor
from lbank_breakout_freshness import evidence, confirmation_allowed, POLICY
from lbank_entry_replay_audit import replay
from test_lbank_entry_quality import structural_state


class BreakoutFreshnessTests(unittest.TestCase):
    def plan(self,d=1,*,dominant=False,ranked=False):
        return dict(direction=d,entry_quality=dict(priority=dict(dominant_intact=dominant),
            ranked_entry=dict(may_override_minor_reaction=ranked)))

    def test_lost_trigger_without_higher_priority_is_rejected_both_sides(self):
        for d in (1,-1):
            p=self.plan(d)
            self.assertFalse(confirmation_allowed(p,dict(breakout_long=False,breakout_short=False)))
            e=p['entry_quality']['breakout_confirmation']
            self.assertEqual(e['policy'],POLICY);self.assertEqual(e['extra_wait_seconds'],0)
            self.assertFalse(e['priority_override_used'])

    def test_original_trigger_still_current_does_not_need_invented_upper_score(self):
        for d in (1,-1):
            p=self.plan(d)
            self.assertTrue(confirmation_allowed(p,dict(breakout_long=d==1,breakout_short=d==-1)))
            self.assertFalse(p['entry_quality']['breakout_confirmation']['priority_override_used'])

    def test_existing_higher_priority_can_support_small_retracement_without_wait(self):
        for d in (1,-1):
            for key in ('dominant','ranked'):
                p=self.plan(d,**{key:True})
                self.assertTrue(confirmation_allowed(p,dict(breakout_long=False,breakout_short=False)))
                self.assertTrue(p['entry_quality']['breakout_confirmation']['priority_override_used'])

    def test_opposite_fresh_breakout_is_not_same_direction_evidence(self):
        p=self.plan(-1)
        self.assertFalse(confirmation_allowed(p,dict(breakout_long=True,breakout_short=False)))

    def test_extracted_initial_predicate_keeps_original_wick_and_momentum_rules(self):
        for d in (1,-1):
            prices=[80000.+d*30*i for i in range(65)]
            bars=[dict(high=p+5,low=p-5) for p in prices]
            state=dict(minute_bars=bars);before=copy.deepcopy((prices,state))
            e=evidence(prices,state,monitor.ema)
            self.assertEqual(e['breakout_long'],d==1)
            self.assertEqual(e['breakout_short'],d==-1)
            bars[-2]['high' if d==1 else 'low']=prices[-1]+d*1
            blocked=evidence(prices,state,monitor.ema)
            self.assertFalse(blocked['breakout_long' if d==1 else 'breakout_short'])
            self.assertEqual(prices,before[0])

    def test_development_incident13_is_cancelled_without_claiming_profit(self):
        bundle=json.loads((Path(__file__).parent/'research/fixtures/lbank_signal13_replay_inputs_20261020.json').read_text())
        result=replay(bundle,monitor.classify,monitor.confirm_watch)
        self.assertEqual(result['actual_confirmation_replay']['reason'],
                         'breakout_trigger_lost_without_higher_priority')
        self.assertFalse(result['actual_confirmation_replay']['accepted'])
        self.assertEqual(result['first_qualified_recorded_quote'],bundle['result']['first_qualified_recorded_quote'])
        self.assertFalse(result['pnl_computed'])


if __name__=='__main__':unittest.main()
