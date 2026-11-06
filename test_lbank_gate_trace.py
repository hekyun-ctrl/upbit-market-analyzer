"""Actual execution coverage/snapshot receipt integration; no network or sends."""
import copy
import hashlib
import json
from pathlib import Path
import sqlite3
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import lbank_futures_monitor as m
from lbank_gate_trace import ExecutionTrace, evaluate, validated_trace
from lbank_live_evidence import LiveEvidenceStore, DisabledEvidence, capture_quote
from lbank_signal_evidence import digest
from test_lbank_live_evidence import envelope

ENABLED=SimpleNamespace(enabled=True)


class TraceTests(unittest.TestCase):
    def test_early_confirmation_records_actual_return_and_unrun_gates(self):
        diag={};result=evaluate(ENABLED,m.confirm_watch,[],{},0,diagnostics=diag,trace_context={})
        self.assertIsNone(result);trace=diag['execution_trace']
        self.assertTrue(trace['complete']);self.assertFalse(trace['branch_boolean_values_available'])
        self.assertEqual(trace['gates']['confirmation']['calls'][0]['reason'],'invalid_watch_age')
        for name in ('momentum','context','rsi','plan','alert_limits'):
            self.assertEqual(trace['gates'][name]['status'],'not_evaluated')
        root=trace['calls'][0]
        self.assertEqual(root['return_source'],'return None')
        self.assertEqual(root['decision_reason'],'invalid_watch_age')
        self.assertEqual(trace['source_sha256']['lbank_futures_monitor.py'],hashlib.sha256(Path(m.__file__).read_bytes()).hexdigest())

    def test_disabled_trace_calls_once_and_leaves_diagnostics_unchanged(self):
        fn=unittest.mock.Mock(return_value=17);diag={}
        self.assertEqual(evaluate(DisabledEvidence(),fn,1,trace_diagnostics=diag),17)
        fn.assert_called_once_with(1);self.assertEqual(diag,{})

    def test_existing_tracer_is_preserved_and_coverage_is_unknown(self):
        previous=sys.gettrace();existing=lambda *a:None
        try:
            sys.settrace(existing);diag={}
            self.assertIsNone(evaluate(ENABLED,m.classify,[],diagnostics=diag))
            self.assertIs(sys.gettrace(),existing)
        finally:sys.settrace(previous)
        t=diag['execution_trace'];self.assertFalse(t['complete'])
        self.assertEqual(t['unavailable_reason'],'existing_thread_tracer')
        self.assertTrue(all(g['reason']=='trace_incomplete_not_observed' for g in t['gates'].values()))

    def test_caps_do_not_change_policy_result_or_imply_unrun_check(self):
        trace=ExecutionTrace(m.confirm_watch,max_calls=0)
        diag={}
        with trace.recording():value=m.confirm_watch([],{},0,diagnostics=diag)
        self.assertIsNone(value);self.assertEqual(diag['reason'],'invalid_watch_age')
        report=trace.report({});self.assertFalse(report['complete'])
        self.assertEqual(report['unavailable_reason'],'trace_call_cap')

    def test_callback_reporting_failure_does_not_retry_policy(self):
        diag={};calls=[]
        def fn():calls.append(1);return 42
        with patch.object(ExecutionTrace,'report',side_effect=OSError('fixture')):
            self.assertEqual(evaluate(ENABLED,fn,trace_diagnostics=diag),42)
        self.assertEqual(calls,[1]);self.assertFalse(diag['execution_trace']['complete'])

    def test_original_exception_propagates_once_and_restores_tracing(self):
        calls=[];previous=sys.gettrace()
        def fn():calls.append(1);raise ValueError('policy_failure_fixture')
        with self.assertRaisesRegex(ValueError,'policy_failure_fixture'):evaluate(ENABLED,fn)
        self.assertEqual(calls,[1]);self.assertIs(sys.gettrace(),previous)

    def test_exact_context_hash_rejects_changed_quote(self):
        context=dict(native_state=None,quote=100.,evaluated_at=300.)
        diag={};evaluate(ENABLED,m.classify,[],diagnostics=diag,trace_context=context)
        self.assertIs(validated_trace(diag['execution_trace'],context),diag['execution_trace'])
        with self.assertRaisesRegex(ValueError,'SNAPSHOT_MISMATCH'):
            validated_trace(diag['execution_trace'],dict(context,quote=101.))

    def test_live_store_persists_complete_trace_and_rejects_context_revision_atomically(self):
        with tempfile.TemporaryDirectory() as directory:
            store=LiveEvidenceStore(Path(directory)/'book.sqlite3')
            try:
                context=dict(native_state=None,quote=100.,evaluated_at=300.)
                diag={};evaluate(ENABLED,m.classify,[],diagnostics=diag,trace_context=context)
                store.append(envelope(diagnostics=diag,reason='insufficient_samples'))
                row=json.loads(store.db.execute('SELECT payload FROM decision_events').fetchone()[0])
                self.assertTrue(row['actual_gate_vector_available'])
                self.assertFalse(row['full_input_receipt_coverage_verified'])
                self.assertTrue(all(v['status']=='not_evaluated' for v in row['gates'].values()))
                with self.assertRaisesRegex(ValueError,'SNAPSHOT_MISMATCH'):
                    store.append(envelope(at=301.,diagnostics=diag))
                self.assertEqual(store.db.execute('SELECT count(*) FROM live_records').fetchone()[0],1)
            finally:store.close()

    def test_public_quote_response_boundary_is_bound_without_fabricating_exchange_wire(self):
        with tempfile.TemporaryDirectory() as directory:
            store=LiveEvidenceStore(Path(directory)/'book.sqlite3')
            class Adapter:
                enabled=True
                def emit(self,kind,data,observed_at):
                    return store.append(dict(version=2,kind=kind,data=data,observed_at=observed_at))
            try:
                ticker=dict(symbol='BTC/USDT:USDT',last=100.,timestamp=299000,percentage=1.,info=dict(secret='excluded'))
                key=capture_quote(Adapter(),ticker,299.,300.)
                self.assertIsNotNone(key)
                store.append(envelope(at=301.,quote_record_hash=key))
                rows=[json.loads(r[0]) for r in store.db.execute('SELECT payload FROM decision_events')]
                self.assertTrue(rows[-1]['quote_normalized_response_receipt_bound'])
                self.assertFalse(rows[-1]['quote_exchange_wire_verified'])
                self.assertNotIn('info',rows[-1]['quote_receipt']['normalized'])
                self.assertFalse(rows[-1]['full_input_receipt_coverage_verified'])
                for value in (envelope(at=301.,quote=101.,quote_record_hash=key),envelope(at=299.,quote_record_hash=key)):
                    with self.assertRaisesRegex(ValueError,'QUOTE_RECEIPT_SNAPSHOT_MISMATCH'):store.append(value)
            finally:store.close()

    def test_missing_quote_receipt_never_silently_backfills_eval_time(self):
        with tempfile.TemporaryDirectory() as directory:
            store=LiveEvidenceStore(Path(directory)/'book.sqlite3')
            try:
                with self.assertRaisesRegex(ValueError,'QUOTE_RECEIPT_RECORD_REQUIRED'):
                    store.append(envelope(quote_record_hash='dropped'))
                self.assertEqual(store.db.execute('SELECT count(*) FROM decision_events').fetchone()[0],0)
            finally:store.close()

    def test_real_native_policy_unchanged_and_helper_returns_are_recorded(self):
        from test_lbank_native_entry import NativeEntryTests
        fixture=NativeEntryTests();fixture.setUp();self.addCleanup(fixture.doCleanups)
        state,_=fixture.snapshot();before=copy.deepcopy(state)
        diag1={};a=m.classify(state['prices'],native_state=state,diagnostics=diag1)
        diag2={};b=evaluate(ENABLED,m.classify,state['prices'],native_state=state,diagnostics=diag2)
        trace=diag2.pop('execution_trace')
        self.assertEqual((a,diag1),(b,diag2));self.assertEqual(state,before)
        self.assertTrue(trace['complete']);self.assertEqual(trace['gates']['context']['status'],'executed')
        self.assertEqual(trace['gates']['rsi']['status'],'executed')
        self.assertEqual(trace['gates']['plan']['status'],'not_evaluated')

    def test_alert_gate_exact_return_and_state_mutation_are_unchanged(self):
        state=dict(day=None,count=0,last_time=None,last_side=None);other=copy.deepcopy(state);diag={}
        a=m.alert_gate(1793159999.,'롱',state)
        b=evaluate(ENABLED,m.alert_gate,1793159999.,'롱',other,trace_diagnostics=diag)
        self.assertEqual(a,b);self.assertEqual(state,other)
        self.assertEqual(diag['execution_trace']['gates']['alert_limits']['calls'][0]['passed'],b=='ok')

    def test_normalized_snapshot_capture_is_optional_and_failure_isolated(self):
        exchange=SimpleNamespace(fetch_ticker=lambda symbol:dict(symbol=symbol,last=100.,timestamp=None,percentage=1.))
        self.assertEqual(m.get_perpetual_snapshot(exchange),(100.,1.))
        class Broken:
            enabled=True
            def emit(self,*a,**kw):raise OSError('fixture')
        quote={};self.assertEqual(m.get_perpetual_snapshot(exchange,evidence=Broken(),quote_evidence=quote),(100.,1.))
        self.assertIsNone(quote['record_hash'])

    def test_trace_install_failure_is_unavailable_and_does_not_skip_original_call(self):
        diag={}
        with patch('lbank_gate_trace.sys.settrace',side_effect=RuntimeError('fixture')):
            self.assertIsNone(evaluate(ENABLED,m.confirm_watch,[],{},0,diagnostics=diag))
        self.assertEqual(diag['reason'],'invalid_watch_age')
        self.assertFalse(diag['execution_trace']['complete'])
        self.assertEqual(diag['execution_trace']['unavailable_reason'],'thread_trace_install_failed')

    def test_frozen_plan_binds_original_decision_chain_and_rejects_plan_revision(self):
        plan=dict(direction=1,strategy='trend_retest',price=100.,stop=99.,target1=102.,target2=103.)
        with tempfile.TemporaryDirectory() as directory:
            store=LiveEvidenceStore(Path(directory)/'book.sqlite3')
            try:
                candidate=store.append(envelope(plan=plan))
                confirmation=store.append(envelope('confirmation',at=301.,plan=plan,candidate_record_hash=candidate))
                eligibility=store.append(envelope('alert_eligibility',at=301.,plan=plan,candidate_record_hash=candidate))
                links=dict(candidate=candidate,confirmation=confirmation,alert_eligibility=eligibility)
                store.append(envelope('frozen_plan',at=301.,plan=plan,candidate_record_hash=candidate,decision_record_hashes=links))
                row=json.loads(store.db.execute('SELECT payload FROM frozen_plans').fetchone()[0])
                self.assertEqual(row['provenance']['executed_decision_links']['candidate']['record_hash'],candidate)
                self.assertFalse(row['provenance']['executed_decision_chain_available'])
                with self.assertRaisesRegex(ValueError,'DECISION_PLAN_MISMATCH'):
                    store.append(envelope('frozen_plan',at=302.,plan=dict(plan,target1=104.),candidate_record_hash=candidate,decision_record_hashes=links))
                self.assertEqual(store.db.execute('SELECT count(*) FROM frozen_plans').fetchone()[0],1)
            finally:store.close()

if __name__=='__main__':unittest.main()
