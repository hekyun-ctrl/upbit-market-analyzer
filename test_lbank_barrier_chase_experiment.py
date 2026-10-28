"""Synthetic E4 boundary and factor-isolation tests; not market evidence."""
import copy
import tempfile
import unittest
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import patch

import lbank_futures_monitor as m
from lbank_entry_location import first_retests
from lbank_signal_evidence import EvidenceLedger
from research.barrier_chase_experiment import (E4Trace, E4E3Trace, E4P1Trace,
    E4E3P1Trace, E4E1Trace, E4E1E3Trace, E4E1P1Trace, E4E1E3P1Trace, replay)
from research.compare_confirmation_experiment import E3Mixin
from test_lbank_entry_location import retest_state


class E4Tests(unittest.TestCase):
    def setUp(self):
        folder=tempfile.TemporaryDirectory();self.addCleanup(folder.cleanup)
        self.ledger=EvidenceLedger(Path(folder.name)/'synthetic.sqlite3');self.addCleanup(self.ledger.close)
        mode=patch.object(m,'ENTRY_REFINEMENT_MODE','capture');mode.start();self.addCleanup(mode.stop)

    def trace(self, cls=E4Trace):
        t=cls(self.ledger,'capture','synthetic',0,{})
        t.now=183900.; t.quote_source=dict(logged_at=replay.iso(t.now),price=80020.3)
        return t

    def plan(self, state, setup, original=None, cls=E4Trace):
        t=self.trace(cls)
        fn=original or m.calculate_plan
        return t,t.plan(fn,state['prices'],setup['direction'],setup=setup,native_state=state)

    def test_chase_bounds_are_symmetric_and_inclusive(self):
        for d in (1,-1):
            s=retest_state(d);atr=first_retests(s)[0]['entry_location']['atr1']
            for strategy,key,limit in [('breakout','level',1.5),('trend_retest','structure_anchor',2.)]:
                for distance,allowed in [(0,False),(-.1,False),(limit-.01,True),(limit+.01,False)]:
                    setup=dict(strategy=strategy,direction=d,**{key:s['quote']-d*distance*atr})
                    t,result=self.plan(s,setup,lambda *a,**kw: {'passed':True})
                    self.assertEqual(result is not None,allowed,(d,strategy,distance))
                    self.assertEqual(t.e4_evidence['location']['passed'],allowed)

    def test_range_rebound_location_is_unchanged(self):
        s=retest_state();setup=dict(strategy='range_rebound',direction=1,structure_anchor=1)
        t,result=self.plan(s,setup,lambda *a,**kw: {'passed':True})
        self.assertIsNotNone(result)
        self.assertEqual(t.e4_evidence['location']['reason'],'range_structure_unchanged')

    def test_malformed_forming_gapped_or_wrong_source_is_rejected(self):
        for change in ('forming','gap','source','nan'):
            s=retest_state()
            if change=='forming':s['minute_bars'][-1]['quality']='forming'
            elif change=='gap':s['minute_bars'][-2]['start']-=60
            elif change=='source':s['source']='spot'
            else:s['quote']=float('nan')
            t,result=self.plan(s,dict(strategy='breakout',direction=1,level=80019),lambda *a,**kw: {'passed':True})
            self.assertIsNone(result,change)
            self.assertEqual(t.e4_evidence['location']['reason'],'location_data_unavailable')

    def test_no_anchor_and_unknown_strategy_proof_is_not_fabricated(self):
        t,result=self.plan(retest_state(),dict(strategy='breakout',direction=1),lambda *a,**kw: {'passed':True})
        self.assertIsNone(result)
        self.assertEqual(t.e4_evidence['location']['reason'],'location_anchor_unavailable')

    def test_only_ranked_pivot_exception_changes_inside_plan(self):
        s=retest_state();setup=first_retests(s)[0];before=copy.deepcopy((s,setup))
        real=m.ranked_entry_evidence;seen=[]
        def inspect(*a,**kw):
            result=m.ranked_entry_evidence(s,1,s['quote']);expected=real(s,1,s['quote'])
            self.assertFalse(result.pop('may_override_single_pivots'))
            expected.pop('may_override_single_pivots');self.assertEqual(result,expected)
            seen.append(True);return {'passed':True}
        t,result=self.plan(s,setup,inspect)
        self.assertIsNotNone(result);self.assertTrue(seen)
        self.assertIs(m.ranked_entry_evidence,real)
        self.assertEqual((s,setup),before)

    def test_exception_restores_ranked_function(self):
        real=m.ranked_entry_evidence
        def error(*a,**kw):raise RuntimeError('synthetic failure')
        with self.assertRaises(RuntimeError):self.plan(retest_state(),first_retests(retest_state())[0],error)
        self.assertIs(m.ranked_entry_evidence,real)

    def test_known_barrier_cannot_use_aligned_direction_in_both_stages(self):
        for d in (1,-1):
            s=retest_state(d);setup=first_retests(s)[0]
            setup.pop('entry_location')
            watch=m.calculate_plan(s['prices'],d,setup,native_state=s)
            self.assertIsNotNone(watch)
            # One unbreached native pivot blocks the unchanged required target.
            s['five_bars'][-6]['high' if d==1 else 'low']=s['quote']+d*150.
            baseline=m.calculate_plan(s['prices'],d,setup,native_state=s)
            self.assertIsNotNone(baseline)
            self.assertIn('single_pivots_under_aligned_15m_5m',baseline['entry_decision_priority']['overrides'])
            for stage_setup in (setup,watch):
                t,result=self.plan(s,stage_setup)
                self.assertIsNone(result)
                self.assertTrue(t.e4_evidence['ranked_plan_evaluations'][-1]['original_may_override_single_pivots'])

    def test_no_barrier_unknown_retains_original_target_panel_behavior(self):
        s=retest_state();setup=first_retests(s)[0];setup.pop('entry_location')
        baseline=m.calculate_plan(s['prices'],1,setup,native_state=s)
        t,result=self.plan(s,setup)
        self.assertIsNotNone(result)
        for key in ('price','stop','target1','target2','quantity_btc','net_loss','net_gain1','rr1'):
            self.assertEqual(result[key],baseline[key],key)

    def test_p1_factor_does_not_fall_back_to_synthesized_targets(self):
        s=retest_state();setup=first_retests(s)[0]
        t,result=self.plan(s,setup,cls=E4P1Trace)
        self.assertIsNone(result)
        self.assertIn(t.last_plan['status'],('room_unknown','room_insufficient'))

    def test_retained_e1_proof_is_validated_at_confirmation_without_mutation(self):
        s=retest_state();setup=first_retests(s)[0];proof=setup.pop('entry_location')
        proof['expires_bar_end']=s['minute_bars'][-1]['start']
        setup['_p2_e1_detection']=proof;before=copy.deepcopy(setup)
        t,result=self.plan(s,setup,lambda *a,**kw: {'passed':True})
        self.assertIsNone(result)
        self.assertEqual(t.e4_evidence['location']['reason'],'first_retest_evidence_lost_or_expired')
        self.assertEqual(setup,before)

    def test_no_bundled_mode_detector_or_confirmation_progress(self):
        s=retest_state();setup=first_retests(s)[0]
        with patch.object(m,'first_retests',side_effect=AssertionError('no detection')),patch.object(m,'location_confirmation_check',side_effect=AssertionError('no progress')):
            self.plan(s,setup)
        self.assertEqual(m.ENTRY_REFINEMENT_MODE,'capture')

    def test_all_eight_factor_names_and_confirmation_age_are_preserved(self):
        for cls,policy in ((E4Trace,'capture_e4'),(E4E3Trace,'capture_e3_e4'),(E4P1Trace,'structural_p1_e4'),(E4E3P1Trace,'structural_p1_e3_e4'),(E4E1Trace,'capture_e1_e4'),(E4E1E3Trace,'capture_e1_e3_e4'),(E4E1P1Trace,'structural_p1_e1_e4'),(E4E1E3P1Trace,'structural_p1_e1_e3_e4')):
            t=self.trace(cls);self.assertEqual(t.policy,policy)
        t=self.trace();calls=[]
        def confirm(prices,watch,age,**kw):calls.append((age,kw));return None
        with patch.object(t,'inputs',return_value='input'),patch.object(t,'record'):
            t.confirm(confirm,retest_state()['prices'],dict(direction=1),7.25,native_state=retest_state(),diagnostics={})
        self.assertEqual(calls[0][0],7.25)
        self.assertNotIn('minimum_confirmation_seconds',calls[0][1])

    def test_context_rsi_momentum_keep_original_precedence_and_no_e4_when_not_evaluated(self):
        for name,value,reason in [('native_context_gate',(False,'synthetic',None),'sampled_context'),('five_minute_rsi_entry_gate',(False,'synthetic',None),'rsi_direction')]:
            s=retest_state();t=self.trace(E4E1Trace);t.now=s['minute_bars'][-1]['start']+60;t.quote_source=dict(logged_at=replay.iso(t.now),price=s['quote'])
            with ExitStack() as hooks:
                t.patches(hooks)
                hooks.enter_context(patch.object(m,'trend_retest_momentum_gate',return_value=(True,'ok',1,1)))
                hooks.enter_context(patch.object(m,name,return_value=value))
                m.classify(s['prices'],native_state=s,diagnostics={})
            rows=[r for r in t.decisions if r.get('phase')=='candidate']
            self.assertEqual(rows[0]['first_reject'],reason)
            self.assertIsNone(rows[0]['e4_evidence'])

    def test_no_dispatch_intent_and_optional_hours_no_gate(self):
        s=retest_state();setup=first_retests(s)[0];a=self.plan(s,setup)[1]
        s.update(hourly_direction=-1,four_hour_direction='UNKNOWN');b=self.plan(s,setup)[1]
        self.assertEqual(a,b)
        self.assertEqual(self.ledger.db.execute('SELECT count(*) FROM dispatch_events').fetchone()[0],0)

if __name__=='__main__':unittest.main()
