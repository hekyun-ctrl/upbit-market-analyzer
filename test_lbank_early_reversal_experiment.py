"""Synthetic E5 causality/isolation tests; never performance samples."""
import copy
import tempfile
import unittest
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import patch

import lbank_futures_monitor as monitor
from lbank_signal_evidence import EvidenceLedger,gate_vector,event_id
from research.early_reversal_experiment import (detect_reversals,frozen_recovery,
    canonical_anchor,E5Mixin,E5_CLASSES,BASE_CLASSES,STRATEGY,replay)
from research.compare_confirmation_experiment import E3Mixin
from test_lbank_entry_location import retest_state


def reversal_state(d=1):
    state=retest_state()
    for b in state['minute_bars'][-15:]:
        b.update(open=80000.,close=80000.,high=80020.,low=79980.)
    state['minute_bars'][-1].update(open=79975.,close=80001.,high=80006.,low=79960.)
    if d==-1:
        for b in state['minute_bars']:
            b.update(open=160000-b['open'],close=160000-b['close'],high=160000-b['low'],low=160000-b['high'])
    state['prices']=[b['close'] for b in state['minute_bars']]
    state['quote']=80007. if d==1 else 79993.
    return state


class E5Tests(unittest.TestCase):
    def setUp(self):
        tmp=tempfile.TemporaryDirectory();self.addCleanup(tmp.cleanup)
        self.ledger=EvidenceLedger(Path(tmp.name)/'evidence.sqlite3');self.addCleanup(self.ledger.close)
        mode=patch.object(monitor,'ENTRY_REFINEMENT_MODE','capture');mode.start();self.addCleanup(mode.stop)

    def now(self,s):return s['minute_bars'][-1]['start']+60
    def proposal(self,s):return detect_reversals(s,self.now(s))[0]
    def trace(self,s,cls=None):
        t=(cls or E5_CLASSES[0])(self.ledger,'capture','synthetic',0,{})
        t.now=self.now(s);t.quote_source=dict(logged_at=replay.iso(t.now),price=s['quote'])
        return t
    def classify(self,t,s):
        with ExitStack() as hooks:
            t.patches(hooks)
            return monitor.classify(s['prices'],native_state=s,diagnostics={})

    def test_both_sides_use_pre_sweep_source_and_quote_without_mutation(self):
        for d in (1,-1):
            s=reversal_state(d);before=copy.deepcopy(s);p=self.proposal(s);f=p['_p2_e5_detection']
            self.assertEqual(p['direction'],d);self.assertEqual(s,before)
            self.assertLess(f['level_source']['start'],f['sweep_source']['start'])
            self.assertEqual(f['trigger'],s['minute_bars'][-1]['high' if d==1 else 'low'])
            self.assertIsNone(f['actual_received_at'])

    def test_missing_sweep_cannot_be_called_reversal(self):
        s=reversal_state();s['minute_bars'][-1]['low']=79990
        self.assertEqual(detect_reversals(s,self.now(s)),[])

    def test_strict_quote_cross_and_reclaim_close_equality(self):
        for d in (1,-1):
            s=reversal_state(d);p=self.proposal(s);s['quote']=p['_p2_e5_detection']['trigger']
            self.assertEqual(detect_reversals(s,self.now(s)),[])
            s=reversal_state(d);s['minute_bars'][-1]['close']=s['minute_bars'][-2]['close'];s['prices'][-1]=s['minute_bars'][-1]['close']
            self.assertEqual(detect_reversals(s,self.now(s)),[])

    def test_bad_source_forming_gap_price_receipt_and_nominal_future_reject(self):
        for kind in ('source','forming','gap','price','receipt','nominal'):
            s=reversal_state();now=self.now(s)
            if kind=='source':s['source']='spot'
            elif kind=='forming':s['minute_bars'][-1]['quality']='forming'
            elif kind=='gap':s['minute_bars'][-2]['start']-=60
            elif kind=='price':s['prices'][-1]+=1
            elif kind=='receipt':s['minute_bars'][-1]['received_at']=now+.001
            else:now-=.001
            self.assertEqual(detect_reversals(s,now),[],kind)

    def test_delay_availability_boundary_is_inclusive(self):
        s=reversal_state();now=self.now(s)
        self.assertEqual(detect_reversals(s,now+14.999,15),[])
        self.assertTrue(detect_reversals(s,now+15,15))

    def test_macro_trend_rsi_do_not_enter_detector(self):
        s=reversal_state();expected=self.proposal(s)
        s['closes15']=[100000,90000,80000];s['rsi5']={'rsi':[90,80,70],'closes':[90000]*20}
        s['one_hour']=None;s['four_hour']={'direction':-1}
        self.assertEqual(self.proposal(s),expected)

    def test_first_reclaim_trigger_and_identity_frozen_on_later_closed_bar(self):
        s=reversal_state();p=self.proposal(s);f=p['_p2_e5_detection'];a=canonical_anchor(f,0)
        b=dict(s['minute_bars'][-1]);b.update(start=b['start']+60,open=80002.,close=80003.,low=79990.,high=80005.)
        s['minute_bars'].append(b);s['prices'].append(b['close'])
        p2=self.proposal(s);self.assertEqual(canonical_anchor(p2['_p2_e5_detection'],0),a)
        self.assertEqual(p2['_p2_e5_detection']['trigger'],f['trigger'])

    def test_new_sweep_against_same_support_is_a_distinct_event(self):
        s=reversal_state();f=self.proposal(s)['_p2_e5_detection'];first=canonical_anchor(f,0)
        second=copy.deepcopy(f);second['sweep_source']['start']+=60
        self.assertNotEqual(event_id(1,STRATEGY,first),event_id(1,STRATEGY,canonical_anchor(second,0)))

    def test_invalid_clock_delay_and_receipt_fail_closed(self):
        for now,delay in ((None,0),(self.now(reversal_state()),float('nan'))):
            self.assertEqual(detect_reversals(reversal_state(),now,delay),[])
        s=reversal_state();s['minute_bars'][-1]['received_at']=float('nan')
        self.assertEqual(detect_reversals(s,self.now(s)),[])

    def test_first_post_recovery_stop_or_level_loss_is_terminal(self):
        for d in (1,-1):
            for kind in ('stop','level'):
                s=reversal_state(d);p=self.proposal(s);f=p['_p2_e5_detection']
                p['stop']=f['structure_anchor']-d*10
                b=dict(s['minute_bars'][-1]);b.update(start=b['start']+60)
                if kind=='stop':b['low' if d==1 else 'high']=p['stop']
                else:b['close']=f['level'];b['open']=f['level']
                s['minute_bars'].append(b);s['prices'].append(b['close'])
                ok,reason,_,terminal=frozen_recovery(s,p,self.now(s))
                self.assertFalse(ok);self.assertTrue(terminal);self.assertEqual(reason,'e5_first_reclaim_invalidated')
                self.assertEqual(detect_reversals(s,self.now(s)),[])

    def test_expiry_and_lost_quote_trigger_are_distinct(self):
        s=reversal_state();p=self.proposal(s);f=p['_p2_e5_detection']
        self.assertTrue(frozen_recovery(s,p,f['expires_at'])[0])
        self.assertEqual(frozen_recovery(s,p,f['expires_at']+.001)[1],'e5_sweep_expired')
        s['quote']=f['trigger'];ok,reason,_,terminal=frozen_recovery(s,p,self.now(s))
        self.assertFalse(ok);self.assertFalse(terminal)

    def test_original_failed_explanatory_gates_are_recorded_only_for_e5(self):
        s=reversal_state();t=self.trace(s);p=self.proposal(s)
        for name in ('context','rsi'):
            t.active=dict(gates=gate_vector())
            reason='avoid_15m_strong_countertrend' if name=='context' else '5m_rsi_direction_mismatch_or_flat'
            with t.scope(p):r=t.gate(name,lambda:(False,reason,17))
            self.assertTrue(r[0]);self.assertEqual(r[2],17)
            self.assertEqual(t.active['gates'][name]['status'],'failed')
            self.assertFalse(t.active['gates'][name]['enforced'])
            t.active=dict(gates=gate_vector());r=t.gate(name,lambda:(False,reason,17))
            self.assertFalse(r[0]);self.assertNotIn('enforced',t.active['gates'][name])

    def test_missing_macro_data_flat_market_and_late_chase_remain_hard(self):
        s=reversal_state();t=self.trace(s);p=self.proposal(s)
        for name,reason in (('context','context_missing_closed_buckets'),('context','flat_sampled_market'),('context','avoid_late_5m_impulse_chase'),('rsi','5m_rsi_direction_data_unavailable')):
            t.active=dict(gates=gate_vector())
            with t.scope(p):result=t.gate(name,lambda:(False,reason,None))
            self.assertFalse(result[0]);self.assertTrue(t.active['gates'][name]['enforced'])

    def test_hard_plan_failure_survives_explanatory_gate_failures(self):
        s=reversal_state();t=self.trace(s)
        with patch.object(monitor,'native_context_gate',return_value=(False,'avoid_15m_strong_countertrend',{})),patch.object(monitor,'five_minute_rsi_entry_gate',return_value=(False,'5m_rsi_direction_mismatch_or_flat',50)),patch.object(monitor,'calculate_plan',return_value=None) as plan:
            self.classify(t,s)
        row=next(x for x in t.decisions if x.get('strategy')==STRATEGY)
        self.assertEqual(row['first_reject'],'risk_or_target');self.assertTrue(plan.called)
        self.assertFalse(row['gates']['context']['enforced']);self.assertFalse(row['gates']['rsi']['enforced'])
        self.assertTrue(row['event_id']);self.assertTrue(row['input_hash'])

    def test_detector_patches_restore_and_no_dispatch(self):
        s=reversal_state();t=self.trace(s);detector=monitor.detect_structures;confirm=monitor.confirm_structure
        self.classify(t,s)
        self.assertIs(monitor.detect_structures,detector);self.assertIs(monitor.confirm_structure,confirm)
        self.assertFalse(t.e5_scope);self.assertIsNone(t.e5_detection)
        self.assertEqual(self.ledger.db.execute('SELECT count(*) FROM dispatch_events').fetchone()[0],0)

    def test_sticky_original_invalidation_blocks_same_event_rearm(self):
        s=reversal_state();t=self.trace(s);p=self.proposal(s);p['_evidence_event_id']='synthetic-event'
        t.e5_invalidated.add('synthetic-event')
        with patch.object(t,'record'):self.assertIsNone(t.schedule(p))
        p['_evidence_event_id']='new-structure'
        self.assertEqual(t.schedule(p),p)

    def test_existing_fresh_breakout_priority_precedes_added_e5(self):
        s=reversal_state();t=self.trace(s);seen=[]
        def assess(prices,d,setup,**kwargs):
            seen.append(setup['strategy']);kwargs['diagnostics']['reason']='synthetic_no_plan';return None
        fresh=dict(breakout_long=True,breakout_short=False,prior_high=80020.,prior_low=79980.,momentum5_pct=0,momentum15_pct=0)
        with patch.object(monitor,'detect_structures',return_value=[]),patch.object(monitor,'breakout_evidence',return_value=fresh),patch.object(monitor,'assess_candidate',side_effect=assess):
            self.classify(t,s)
        self.assertEqual(seen,['breakout',STRATEGY])

    def test_preempted_confirmation_does_not_reuse_prior_e5_readiness(self):
        s=reversal_state();t=self.trace(s,E5_CLASSES[1]);p=self.proposal(s)
        p.update(stop=79950.,native_watch_1m_start=s['minute_bars'][-1]['start'])
        t.e5_readiness={'stale':'previous event'}
        with patch.object(t,'inputs',return_value='input'),patch.object(t,'record') as record:
            t.confirm(lambda *a,**kw:None,s['prices'],p,0,native_state=s,diagnostics={})
        self.assertIsNone(t.e5_readiness)

    def test_all16_combinations_have_distinct_expected_names_and_clocks(self):
        s=reversal_state();names=set()
        for base,cls in zip(BASE_CLASSES,E5_CLASSES):
            t=self.trace(s,cls);b=base(self.ledger,'capture','synthetic',0,{})
            self.assertEqual(t.policy,b.policy+'_e5');names.add(t.policy)
            self.assertEqual(isinstance(t,E3Mixin),isinstance(b,E3Mixin))
        self.assertEqual(len(names),16)

    def test_actual_age_and_default_wait_are_passed_unchanged(self):
        s=reversal_state();p=self.proposal(s);t=self.trace(s);calls=[]
        def existing(prices,watch,age,**kw):calls.append((age,kw));return None
        with patch.object(t,'inputs',return_value='input'),patch.object(t,'record'):
            t.confirm(existing,s['prices'],p,7.25,native_state=s,diagnostics={})
        self.assertEqual(calls[0][0],7.25);self.assertNotIn('minimum_confirmation_seconds',calls[0][1])


if __name__=='__main__':unittest.main()
