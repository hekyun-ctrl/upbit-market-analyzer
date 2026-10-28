"""Synthetic E3 safety/causality tests, never profitability observations."""
import copy
import ast
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import lbank_futures_monitor as m
from lbank_native_entry import SOURCE
from lbank_signal_evidence import EvidenceLedger
from research.evidence_confirmation import freeze_trigger, readiness, POLICY
from research.compare_confirmation_experiment import E3Trace, pair_evidence
from research.render_confirmation_report import fixed_plan_entry


def fixture(direction=1):
    bars=[dict(start=600+i*60,open=100.,close=100.,high=101.,low=99.,quality='stream_closed') for i in range(4)]
    state=dict(source=SOURCE,quote=102. if direction==1 else 98.,minute_bars=bars)
    setup=dict(direction=direction,strategy='breakout',level=101. if direction==1 else 99.)
    watch=dict(setup,price=state['quote'],stop=95. if direction==1 else 105.,native_watch_1m_start=780)
    watch['_p2_confirmation_trigger']=freeze_trigger(setup,state)
    return state,watch


class E3ReadinessTests(unittest.TestCase):
    def test_ready_both_directions_without_wait_or_synthetic_candle(self):
        for d in (1,-1):
            state,watch=fixture(d);before=copy.deepcopy((state,watch))
            ok,reason,proof,terminal=readiness(state,watch,840)
            self.assertTrue(ok);self.assertEqual(reason,'e3_trigger_ready')
            self.assertIsNone(proof['actual_received_at']);self.assertIsNone(proof['known_at'])
            self.assertFalse(terminal);self.assertEqual((state,watch),before)

    def test_frozen_trigger_cannot_follow_later_quote_or_bar(self):
        state,watch=fixture();frozen=copy.deepcopy(watch['_p2_confirmation_trigger'])
        state['quote']=101.;state['minute_bars'][-1]['high']=110.
        self.assertEqual(readiness(state,watch,840)[1],'e3_frozen_trigger_not_crossed')
        self.assertEqual(watch['_p2_confirmation_trigger'],frozen)

    def test_nonbreakout_trigger_uses_discovery_extreme(self):
        for d in (1,-1):
            state,watch=fixture(d);setup=dict(direction=d,strategy='trend_retest',level=900.)
            proof=freeze_trigger(setup,state)
            self.assertEqual(proof['price'],101. if d==1 else 99.)
            self.assertEqual(proof['basis'],'last_completed_minute_directional_extreme')

    def test_first_post_watch_bar_stop_cannot_be_erased_by_recovery(self):
        for d in (1,-1):
            state,watch=fixture(d)
            first=dict(start=840,open=100.,close=100.,high=106. if d==-1 else 101.,low=94. if d==1 else 99.,quality='stream_closed')
            state['minute_bars'].append(first)
            ok,reason,_,terminal=readiness(state,watch,900)
            self.assertFalse(ok);self.assertEqual(reason,'e3_original_stop_crossed');self.assertTrue(terminal)

    def test_quote_stop_is_terminal(self):
        state,watch=fixture();state['quote']=95.
        self.assertEqual(readiness(state,watch,840)[1::2],('e3_original_stop_crossed',True))

    def test_future_receipt_forming_gap_wrong_source_and_bad_prices_reject(self):
        for change in ('future_close','receipt','forming','gap','source','nan','ohlc','missing_anchor'):
            state,watch=fixture()
            if change=='future_close':now=839
            else:now=840
            if change=='receipt':state['minute_bars'][-1]['received_at']=841
            elif change=='forming':state['minute_bars'][-1]['quality']='forming'
            elif change=='gap':state['minute_bars'][-2]['start']-=60
            elif change=='source':state['source']='spot'
            elif change=='nan':state['quote']=float('nan')
            elif change=='ohlc':state['minute_bars'][-1]['low']=102.
            elif change=='missing_anchor':watch['native_watch_1m_start']=300
            result=readiness(state,watch,now)
            self.assertFalse(result[0],change);self.assertTrue(result[3],change)

    def test_nominal_delay_remains_assumption_and_future_bars_unavailable(self):
        state,watch=fixture()
        self.assertFalse(readiness(state,watch,850,15)[0])
        self.assertTrue(readiness(state,watch,855,15)[0])

    def test_live_default_wait_and_explicit_research_age_are_separate(self):
        watch=dict(direction=1)
        diag={};self.assertIsNone(m.confirm_watch([],watch,0,diagnostics=diag))
        self.assertEqual(diag['reason'],'invalid_watch_age')
        diag={};self.assertIsNone(m.confirm_watch([],watch,0,diagnostics=diag,minimum_confirmation_seconds=0))
        self.assertEqual(diag['reason'],'insufficient_samples')
        for minimum in (-1,15,31,float('nan')):
            diag={};self.assertIsNone(m.confirm_watch([],watch,30,diagnostics=diag,minimum_confirmation_seconds=minimum))
            self.assertEqual(diag['reason'],'invalid_watch_age')

    def test_nan_age_never_confirms(self):
        diag={};self.assertIsNone(m.confirm_watch([],{},float('nan'),diagnostics=diag,minimum_confirmation_seconds=0))
        self.assertEqual(diag['reason'],'invalid_watch_age')

    def test_research_zero_wait_still_runs_real_cost_and_quote_gates(self):
        from test_lbank_native_entry import NativeEntryTests
        fixture=NativeEntryTests();fixture.setUp();self.addCleanup(fixture.doCleanups)
        state,_=fixture.snapshot()
        state['rsi5']['rsi']=[83.,84.,85.]
        watch=m.classify(state['prices'],native_state=state)
        self.assertIsNotNone(watch)
        with patch.object(m,'calculate_plan',return_value=None) as risk:
            diag={}
            self.assertIsNone(m.confirm_watch(state['prices'],watch,0,native_state=state,
                diagnostics=diag,minimum_confirmation_seconds=0))
            self.assertEqual(diag['reason'],'risk_or_target');risk.assert_called_once()
        moved=copy.deepcopy(state);moved['quote']=watch['price']*1.01
        diag={}
        self.assertIsNone(m.confirm_watch(moved['prices'],watch,0,native_state=moved,
            diagnostics=diag,minimum_confirmation_seconds=0))
        self.assertEqual(diag['reason'],'price_deviation')

    def test_production_has_no_e3_activation_call_or_import(self):
        source=Path('lbank_futures_monitor.py').read_text();tree=ast.parse(source)
        calls=[n for n in ast.walk(tree) if isinstance(n,ast.Call)
               and isinstance(n.func,ast.Name) and n.func.id=='confirm_watch']
        self.assertTrue(calls)
        self.assertFalse(any(k.arg=='minimum_confirmation_seconds' for n in calls for k in n.keywords))
        self.assertNotIn('research.evidence_confirmation',source)

    def test_e3_keeps_actual_age_and_calls_existing_checks(self):
        state,watch=fixture();watch['_evidence_event_id']=None
        with tempfile.TemporaryDirectory() as directory:
            ledger=EvidenceLedger(Path(directory)/'ledger.sqlite3')
            trace=E3Trace(ledger,'capture','synthetic',0,{})
            trace.now=840;trace.quote_source={'logged_at':'1970-01-01T00:14:00Z'}
            with patch.object(trace,'inputs',return_value='synthetic-input'),patch.object(trace,'record') as record:
                calls=[]
                def original(prices,watch,age,**kwargs):
                    calls.append((age,kwargs['minimum_confirmation_seconds']))
                    kwargs['diagnostics']['reason']='risk_or_target'
                    return None
                diag={}
                self.assertIsNone(trace.confirm(original,[],watch,7.25,native_state=state,diagnostics=diag))
                self.assertEqual(calls,[(7.25,0)]);self.assertEqual(diag['reason'],'risk_or_target')
                self.assertEqual(record.call_args.kwargs['phase'],'confirmation')
            ledger.close()

    def test_e3_stop_failure_cancels_watch_without_calling_remaining_checks(self):
        state,watch=fixture();state['quote']=94.
        with tempfile.TemporaryDirectory() as directory:
            ledger=EvidenceLedger(Path(directory)/'ledger.sqlite3')
            trace=E3Trace(ledger,'capture','synthetic',0,{})
            trace.now=840
            with patch.object(trace,'inputs',return_value='input'),patch.object(trace,'record'):
                original=unittest.mock.Mock()
                diag={};self.assertIsNone(trace.confirm(original,[],watch,0,native_state=state,diagnostics=diag))
                original.assert_not_called();self.assertTrue(diag['cancel_watch'])
            ledger.close()

    def test_exact_event_pairing_retains_rejections_and_no_fake_match(self):
        cards=[dict(plan_id='old',event_id='a',policy='capture'),dict(plan_id='new',event_id='b',policy='capture_e3')]
        decisions=[dict(event_id='a',policy='capture_e3',first_reject='e3_original_stop_crossed',evaluated_at=840)]
        groups=pair_evidence(cards,decisions,'capture','capture_e3')
        self.assertEqual([g['group'] for g in groups],['excluded','unmatched'])
        self.assertEqual(groups[0]['gate_reasons'][0]['first_reject'],'e3_original_stop_crossed')

    def test_terminal_stop_blocks_same_event_rearm_after_quote_recovers(self):
        state,watch=fixture();state['quote']=94.;watch['_evidence_event_id']='broken-event'
        with tempfile.TemporaryDirectory() as directory:
            ledger=EvidenceLedger(Path(directory)/'ledger.sqlite3')
            trace=E3Trace(ledger,'capture','synthetic',0,{})
            trace.now=840
            with patch.object(trace,'inputs',return_value='input'),patch.object(trace,'record') as record:
                trace.confirm(unittest.mock.Mock(),[],watch,0,native_state=state,diagnostics={})
                recovered=dict(watch,price=102.)
                self.assertIsNone(trace.schedule(recovered))
                self.assertEqual(record.call_args.args[2],'e3_event_previously_invalidated')
                fresh=dict(recovered,_evidence_event_id='new-structure-event')
                self.assertIs(trace.schedule(fresh),fresh)
            ledger.close()

    def test_runner_filters_precomputed_proposal_after_terminal_confirmation(self):
        from research.compare_entry_refinement import replay_window
        from research import compare_entry_refinement as replay
        proposal=dict(direction=1,strategy='breakout',side='LONG',price=102.,_evidence_event_id='broken')
        state=dict(prices=[100.]*65,minute_bars=[dict(start=780)])
        terminal=[False]
        def classify(*args,**kwargs):
            kwargs['diagnostics'].update(reason='qualified',candidates=[])
            return copy.deepcopy(proposal)
        def confirm(*args,**kwargs):
            terminal[0]=True
            kwargs['diagnostics'].update(reason='e3_original_stop_crossed',cancel_watch=True)
            return None
        def schedule(value):return None if terminal[0] else value
        points=[dict(logged_at='1970-01-01T00:14:00Z',price=102.),
                dict(logged_at='1970-01-01T00:14:31Z',price=102.)]
        with patch.object(replay,'available_state',return_value=(state,'valid')), \
             patch.object(m,'classify',side_effect=classify),patch.object(m,'confirm_watch',side_effect=confirm), \
             patch.object(m,'alert_gate',return_value='ok'), \
             patch.object(replay,'compact_plan',return_value={'target1_distance_pct':.1}):
            result=replay_window(points,None,0,proposal_filter=schedule)
        self.assertEqual(result['watches'],1)
        self.assertEqual(result['pending_at_end'],0)


class FixedPlanTimingTests(unittest.TestCase):
    def cards(self,entry=100.):
        shared=dict(plan=dict(direction=1,price=100.,stop=95.,target1=115.,target2=120.,quantity_btc=1.))
        observed=dict(plan=dict(price=entry),provenance=dict(confirm_at_source_text='2026-10-22T04:36:45.742018702Z'))
        return shared,observed

    def test_no_entry_is_zero_without_evaluating_an_outcome(self):
        shared,observed=self.cards(114.)
        with patch('research.render_confirmation_report.replay.outcome') as outcome:
            result=fixed_plan_entry(shared,observed,{})
            self.assertEqual(result['status'],'no_entry');self.assertEqual(result['net_pnl_model'],0)
            outcome.assert_not_called()

    def test_unresolved_entry_stays_null_and_source_time_is_exact(self):
        shared,observed=self.cards()
        with patch('research.render_confirmation_report.replay.outcome',return_value={'status':'unresolved'}) as outcome:
            result=fixed_plan_entry(shared,observed,{})
            self.assertIsNone(result['net_pnl_model'])
            self.assertEqual(outcome.call_args.args[0]['at'],observed['provenance']['confirm_at_source_text'])
            self.assertEqual(outcome.call_args.args[0]['target1'],115.)
            self.assertLessEqual(result['quantity_btc'],shared['plan']['quantity_btc'])

    def test_fixed_target_exact_outcome_is_preserved(self):
        shared,observed=self.cards()
        with patch('research.render_confirmation_report.replay.outcome',return_value={'status':'exact_in_model','net_usdt_low':-1.25}):
            result=fixed_plan_entry(shared,observed,{})
            self.assertEqual(result['net_pnl_model'],-1.25)


if __name__=='__main__':unittest.main()
