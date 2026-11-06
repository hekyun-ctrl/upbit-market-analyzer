"""Synthetic E1 isolation/causality checks; no performance observations."""
import copy
import tempfile
import unittest
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import patch

import lbank_futures_monitor as monitor
from lbank_entry_location import first_retests
from lbank_signal_evidence import EvidenceLedger
from research.first_retest_experiment import (merge_proposals, E1Trace, E1P1Trace,
    E1E3Trace, E1E3P1Trace, replay)
from research.compare_confirmation_experiment import E3Mixin
from test_lbank_entry_location import retest_state


class E1IsolationTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.ledger = EvidenceLedger(Path(directory.name)/'evidence.sqlite3')
        self.addCleanup(self.ledger.close)
        mode = patch.object(monitor, 'ENTRY_REFINEMENT_MODE', 'capture')
        mode.start(); self.addCleanup(mode.stop)

    def trace(self, cls, state):
        trace = cls(self.ledger, 'capture', 'synthetic', 0, {})
        trace.now = state['minute_bars'][-1]['start']+60
        trace.quote_source = dict(logged_at=replay.iso(trace.now), price=state['quote'])
        return trace

    def run_classify(self, trace, state):
        diagnostics = {}
        with ExitStack() as hooks:
            trace.patches(hooks)
            result = monitor.classify(state['prices'], native_state=state, diagnostics=diagnostics)
        return result, diagnostics

    def test_native_retest_both_sides_and_capture_fallback_deduplicate_without_mutation(self):
        for d in (1, -1):
            state = retest_state(d)
            fallback = [dict(strategy='trend_retest', direction=d, structure_anchor=1),
                        dict(strategy='breakout', direction=-d, level=2)]
            before = copy.deepcopy((state, fallback))
            merged = merge_proposals(state, fallback)
            self.assertEqual(merged[0], first_retests(state)[0])
            self.assertEqual(merged[1], fallback[1])
            self.assertEqual(len(merged), 2)
            self.assertEqual((state, fallback), before)

    def test_bad_or_forming_native_state_does_not_create_retest(self):
        for change in ('forming', 'gap', 'source', 'future_price'):
            state = retest_state()
            if change == 'forming': state['minute_bars'][-1]['quality'] = 'forming'
            elif change == 'gap': state['minute_bars'][-2]['start'] -= 60
            elif change == 'source': state['source'] = 'spot'
            else: state['prices'][-1] += 1
            fallback = [dict(strategy='breakout', direction=1, level=1)]
            self.assertEqual(merge_proposals(state, fallback), fallback)

    def test_actual_quote_trigger_does_not_append_a_bar(self):
        for d in (1, -1):
            state = retest_state(d)
            b = state['minute_bars'][-1]
            b.update(open=80019.2 if d==1 else 79980.8,
                     close=80019.3 if d==1 else 79980.7,
                     high=80019.4 if d==1 else 79981.1,
                     low=80018.9 if d==1 else 79980.6)
            state['prices'] = [b['close'] for b in state['minute_bars']]
            state['quote'] = 80020.1 if d==1 else 79979.9
            before = copy.deepcopy(state)
            proposals = merge_proposals(state, [])
            self.assertEqual(proposals[0]['entry_location']['source'], 'completed_retest_live_quote')
            self.assertEqual(state, before)

    def test_detector_injection_is_scoped_and_keeps_capture_mode(self):
        state = retest_state(); trace = self.trace(E1Trace, state)
        detector = monitor.detect_structures
        self.run_classify(trace, state)
        self.assertIs(monitor.detect_structures, detector)
        self.assertEqual(monitor.ENTRY_REFINEMENT_MODE, 'capture')
        candidates = [d for d in trace.decisions if d.get('phase') == 'candidate']
        self.assertTrue(any(d.get('e1_detection', {}).get('first_retest') for d in candidates))
        self.assertTrue(all(d['input_hash'] for d in candidates))
        self.assertTrue(any(d['event_id'] for d in candidates if d.get('e1_detection')))

    def test_failed_capture_momentum_is_not_bypassed_for_first_retest(self):
        state = retest_state(); trace = self.trace(E1Trace, state)
        with patch.object(monitor, 'trend_retest_momentum_gate', return_value=(False,'synthetic_weak',0.,0.)), \
             patch.object(monitor, 'direction_priority', return_value={'dominant_intact':False}), \
             patch.object(monitor, 'ranked_entry_evidence', return_value={'may_override_minor_reaction':False}):
            self.run_classify(trace, state)
        rows = [d for d in trace.decisions if d.get('phase')=='candidate' and d.get('e1_detection')]
        self.assertEqual(rows[0]['first_reject'], 'retest_momentum')
        self.assertEqual(rows[0]['gates']['momentum']['status'], 'failed')

    def test_location_gates_are_never_enabled_by_detection(self):
        state = retest_state(); trace = self.trace(E1Trace, state)
        with patch.object(monitor, 'location_check', side_effect=AssertionError('E4 gate mixed in')):
            self.run_classify(trace, state)
        self.assertEqual(monitor.ENTRY_REFINEMENT_MODE, 'capture')

    def test_candidates_retain_context_rsi_and_real_cost_risk_gates(self):
        for name, failure, expected in (
            ('native_context_gate', (False,'synthetic_context',None), 'sampled_context'),
            ('five_minute_rsi_entry_gate', (False,'synthetic_rsi',None), 'rsi_direction'),
            ('calculate_plan', None, 'risk_or_target')):
            state = retest_state(); trace = self.trace(E1Trace, state)
            with patch.object(monitor, 'trend_retest_momentum_gate', return_value=(True,'synthetic_ok',1.,1.)), \
                 patch.object(monitor, name, return_value=failure) as gate:
                self.run_classify(trace, state)
            rows = [d for d in trace.decisions if d.get('phase')=='candidate' and d.get('e1_detection')]
            self.assertEqual(rows[0]['first_reject'], expected)
            self.assertTrue(gate.called)

    def test_arm_names_separate_confirmation_and_target_factors(self):
        state = retest_state()
        for cls, policy, fast in ((E1Trace,'capture_e1',False),
            (E1E3Trace,'capture_e1_e3',True),(E1P1Trace,'structural_p1_e1',False),
            (E1E3P1Trace,'structural_p1_e1_e3',True)):
            trace = self.trace(cls, state)
            self.assertEqual(trace.policy, policy)
            self.assertEqual(isinstance(trace,E3Mixin), fast)

    def test_confirmation_keeps_actual_age_and_original_default_wait(self):
        state = retest_state(); trace = self.trace(E1Trace, state)
        watch = dict(direction=1, strategy='trend_retest', _p2_e1_detection={'first_retest':True})
        with patch.object(trace, 'inputs', return_value='input'), patch.object(trace,'record'):
            calls=[]
            def existing(prices,watch,age,**kwargs):
                calls.append((age,kwargs));return None
            trace.confirm(existing,state['prices'],watch,7.25,native_state=state,diagnostics={})
            self.assertEqual(calls[0][0],7.25)
            self.assertNotIn('minimum_confirmation_seconds',calls[0][1])

    def test_exception_restores_detector_and_no_dispatch_intent(self):
        state=retest_state();trace=self.trace(E1Trace,state);detector=monitor.detect_structures
        with patch.object(trace,'inputs',side_effect=RuntimeError('synthetic outage')):
            with self.assertRaises(RuntimeError):trace.classify(monitor.classify,state['prices'],native_state=state)
        self.assertIs(monitor.detect_structures,detector)
        self.assertEqual(self.ledger.db.execute('SELECT count(*) FROM dispatch_events').fetchone()[0],0)


if __name__ == '__main__': unittest.main()
