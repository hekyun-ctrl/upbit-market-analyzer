"""Original scheduler boundaries, exact source and isolated durable state."""
import ast
import copy
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from lbank_signal_evidence import EvidenceLedger,canonical
from research.continuous_comparison import ContinuousComparison,declaration,policy_spec,replay
from research.original_live_schedule import OriginalLiveComparison,config,source_slice,runtime_state,stored_poll_points
from lbank_live_evidence import LiveEvidenceStore,VERSION,observe,DisabledEvidence
import test_lbank_native_entry as native_fixture


class OriginalScheduleTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        bundles,_=replay.load_archives(replay.DATA/'archive_chunks.json')
        cls.costs=bundles[0]['model_config']

    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.path=Path(self.tmp.name)/'book.db';self.book=EvidenceLedger(self.path)
        self.addCleanup(lambda:self.book.close())
        self.fixture=native_fixture.NativeEntryTests();self.fixture.setUp();self.addCleanup(self.fixture.doCleanups)
        self.state,_=self.fixture.snapshot();self.now=self.fixture.now
        self.manifest=declaration([policy_spec(0),policy_spec(2)],self.costs,
            schedule='original_live_entry_ast_v1',schedule_config=config(poll_seconds=60))
        self.runner=OriginalLiveComparison(self.book,self.manifest)
        def no_proposal(*args,diagnostics=None,**kwargs):
            if diagnostics is not None:diagnostics.update(reason='market_no_setup',candidates=[])
            return None
        self.addCleanup(patch.stopall)
        patch.object(replay.monitor,'classify',side_effect=no_proposal).start()
        patch.object(replay.monitor,'telegram',side_effect=AssertionError('OUTBOUND_FORBIDDEN')).start()

    def point(self,seq=0,at=None,**extra):
        at=self.now+seq if at is None else at
        return dict(seq=seq,observed_at=at,quote=self.state['quote'],native_state=copy.deepcopy(self.state),
                    source_observation=dict(logged_at=replay.iso(at),price=self.state['quote']),**extra)

    def seed(self,**values):
        for spec in self.manifest['policies']:
            name=spec['class_name'];s=self.runner.state(name);r=runtime_state(self.runner.settings)
            r.update(last_sample=self.now,observed_ready_key=list(replay.monitor.completed_key(self.state)),
                     last_setup_key=list(replay.monitor.completed_key(self.state)),first_ready_observed_at=self.now)
            r.update(copy.deepcopy(values));s['original_live_runtime']=r
            self.book.db.execute('UPDATE comparison_states SET payload=? WHERE run_id=? AND policy=?',
                (canonical(s),self.runner.identity,name))
        self.book.db.commit()

    def watch(self,age):
        return dict(time=self.now-age,plan=dict(price=self.state['quote'],side='롱',direction=1,
            strategy='breakout',stop=self.state['quote']*.99))

    def test_entry_branch_ast_is_entire_original_branch_without_statement_rewrite(self):
        tree=ast.parse(Path(replay.monitor.__file__).read_text())
        run=next(n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name=='run')
        original=next(n for n in ast.walk(run) if isinstance(n,ast.If) and
                      ast.unparse(n.test)=='sample_due or confirm_due or setup_due')
        self.assertEqual(ast.dump(original),ast.dump(source_slice().body[-1]))

    def test_new_completed_key_evaluates_before_periodic_timer(self):
        self.seed(last_setup_key=None)
        rows=self.runner.step(self.point())
        self.assertTrue(all(not r['schedule']['sample_due'] and r['schedule']['new_setup_bar']
                            and r['actual_policy_call_order']==['classify'] for r in rows))

    def test_no_due_poll_is_retained_without_reexecuting_policy(self):
        self.seed();rows=self.runner.step(self.point())
        self.assertTrue(all(r['status']=='no_entry_evaluation_due' and r['actual_policy_call_order']==[] for r in rows))
        self.assertEqual(self.book.db.execute('SELECT count(*) FROM comparison_observations').fetchone()[0],1)

    def test_sample_exact_60_second_boundary(self):
        self.seed(last_sample=self.now-60)
        self.assertTrue(all(r['schedule']['sample_due'] for r in self.runner.step(self.point())))

    def test_sample_just_before_boundary_has_no_entry_evaluation(self):
        self.seed(last_sample=self.now-59.999)
        self.assertTrue(all(r['status']=='no_entry_evaluation_due' for r in self.runner.step(self.point())))

    def test_watch_30_second_boundary_confirms_before_any_candidate(self):
        self.seed(watch=self.watch(30))
        def no_confirm(*args,diagnostics,**kwargs):diagnostics['reason']='await_two_post_watch_closed_minutes'
        with patch.object(replay.monitor,'confirm_watch',side_effect=no_confirm):
            rows=self.runner.step(self.point())
        self.assertTrue(all(r['schedule']['confirm_due'] and r['actual_policy_call_order']==['confirm_watch'] for r in rows))

    def test_under30_watch_not_called_without_other_due_trigger(self):
        self.seed(watch=self.watch(29.999))
        self.assertTrue(all(r['actual_policy_call_order']==[] for r in self.runner.step(self.point())))

    def test_periodic_tick_can_call_original_confirmation_under30(self):
        self.seed(watch=self.watch(10),last_sample=self.now-60)
        def no_confirm(*args,diagnostics,**kwargs):diagnostics['reason']='watch_too_young'
        with patch.object(replay.monitor,'confirm_watch',side_effect=no_confirm):rows=self.runner.step(self.point())
        self.assertTrue(all(not r['schedule']['confirm_due'] and r['actual_policy_call_order']==['confirm_watch'] for r in rows))

    def test_expired_watch_can_reclassify_same_periodic_poll(self):
        self.seed(watch=self.watch(241),last_sample=self.now-60)
        rows=self.runner.step(self.point())
        self.assertTrue(all(r['counters']['expired']==1 and r['actual_policy_call_order']==['classify'] for r in rows))

    def test_original_terminal_confirmation_rejection_can_reclassify(self):
        self.seed(watch=self.watch(31),last_sample=self.now-60)
        def reject(*args,diagnostics,**kwargs):diagnostics['reason']=replay.monitor.BREAKOUT_CONFIRMATION_REJECT
        with patch.object(replay.monitor,'confirm_watch',side_effect=reject):rows=self.runner.step(self.point())
        self.assertTrue(all(r['actual_policy_call_order']==['confirm_watch','classify'] for r in rows))

    def test_journal_unavailable_clears_watch_and_never_calls_strategy(self):
        self.seed(watch=self.watch(31))
        rows=self.runner.step(self.point(journal_available=False))
        self.assertTrue(all(r['actual_policy_call_order']==[] and not r['valid_interval']
                            and r['counters']['journal_unavailable']==1 for r in rows))
        self.assertTrue(all(self.runner.state(p['class_name'])['watch'] is None for p in self.manifest['policies']))

    def test_data_gap_clears_watch_with_null_economics(self):
        self.seed(watch=self.watch(31));p=self.point();p['native_state']=None
        rows=self.runner.step(p)
        self.assertTrue(all(r['modeled_net_usdt'] is None and r['actual_policy_call_order']==[] for r in rows))

    def test_restart_restores_all_schedule_keys_and_cache_not_only_watch(self):
        self.runner.step(self.point());before=self.runner.report();names=[p['class_name'] for p in self.manifest['policies']]
        states={n:self.runner.state(n) for n in names}
        self.book.close();self.book=EvidenceLedger(self.path);self.runner=OriginalLiveComparison(self.book,self.manifest)
        self.assertEqual(before,self.runner.report())
        self.assertEqual(states,{n:self.runner.state(n) for n in names})

    def test_second_policy_failure_rolls_back_schedule_state_cursor_and_evidence(self):
        before=self.runner.report();fn=self.runner._step_policy;calls=[]
        def fail(spec,current,p):
            calls.append(1)
            if len(calls)==2:raise sqlite3.OperationalError('fixture rollback')
            return fn(spec,current,p)
        with patch.object(self.runner,'_step_policy',side_effect=fail):
            with self.assertRaises(sqlite3.OperationalError):self.runner.step(self.point())
        self.assertEqual(before,self.runner.report())
        for table in ('decision_events','comparison_observations','source_events'):
            self.assertEqual(self.book.db.execute('SELECT count(*) FROM '+table).fetchone()[0],0)

    def test_idempotent_poll_preserves_complete_state_and_rejects_revision(self):
        p=self.point();rows=self.runner.step(p);state=self.runner.report()
        self.assertEqual(rows,self.runner.step(p));self.assertEqual(state,self.runner.report())
        with self.assertRaisesRegex(ValueError,'QUOTE_STATE_MISMATCH|OBSERVATION_REVISION'):
            self.runner.step(dict(p,quote=p['quote']+1))

    def test_manifest_cannot_claim_unsupported_e3_or_e5_schedule(self):
        for policies in ([policy_spec(0),policy_spec(1)],[policy_spec(0),policy_spec(0,e5=True)]):
            m=declaration(policies,self.costs,schedule=self.manifest['schedule'],schedule_config=self.runner.settings)
            with self.assertRaisesRegex(ValueError,'EXPERIMENTAL_SCHEDULE'):OriginalLiveComparison(self.book,m)
        with self.assertRaisesRegex(ValueError,'ORIGINAL_LIVE_ADAPTER_REQUIRED'):
            ContinuousComparison(self.book,self.manifest)

    def test_unseen_does_not_execute_policy_or_turn_asserted_hashes_into_receipts(self):
        m=declaration(self.manifest['policies'],self.costs,purpose='unseen',
            schedule=self.manifest['schedule'],schedule_config=self.runner.settings)
        r=OriginalLiveComparison(self.book,m);rows=r.step(self.point(quote_record_hash='asserted',received_snapshot_hash='asserted'))
        self.assertTrue(all(not x['qualification']['qualified'] and x['modeled_net_usdt'] is None and x['phases']==[] for x in rows))

    def test_source_config_revision_and_invalid_clock_rejected(self):
        for value in (float('nan'),float('inf'),True,-1):
            with self.assertRaises(ValueError):config(poll_seconds=60,initial_last_sample=value)
        bad=copy.deepcopy(self.manifest);bad['schedule_config']['source_ast_sha256']='changed'
        with self.assertRaisesRegex(ValueError,'SOURCE_CHANGED'):OriginalLiveComparison(self.book,bad)

    def delivery_case(self,model):
        # Synthetic scheduler routing using a retained plan as a field-complete
        # fixture. It is neither a new trade nor a new policy performance test.
        report=json.loads((Path(__file__).parent/'research/zerobase/completion_20261028/gate_trace/continuous_report.json').read_text())
        plan=copy.deepcopy(report['pairing'][0]['old']['plan'])
        plan['structural_target_plan']=dict(barrier=dict(price=plan['target1']),
            target1_basis='synthetic_scheduler_fixture_only')
        plan['_evidence_candidate_at']=self.now-31
        plan['_evidence_candidate_source_time']=replay.iso(self.now-31)
        if model=='modeled_failure':
            self.manifest=declaration(self.manifest['policies'],self.costs,schedule=self.manifest['schedule'],
                schedule_config=config(poll_seconds=60,delivery_model=model))
            self.runner=OriginalLiveComparison(self.book,self.manifest)
        self.seed(watch=dict(time=self.now-31,plan=plan))
        def confirmed(*args,**kwargs):return copy.deepcopy(plan)
        with patch.object(replay.monitor,'confirm_watch',side_effect=confirmed),\
             patch.object(replay.monitor,'alert_gate',return_value='ok'):
            return self.runner.step(self.point())

    def test_modeled_success_advances_only_offline_cooldown_and_unresolved_entry(self):
        rows=self.delivery_case('modeled_success')
        self.assertTrue(all(not r['no_entry'] and len(r['plan_ids'])==1 for r in rows))
        for p in self.manifest['policies']:
            state=self.runner.state(p['class_name'])
            self.assertEqual(state['alert']['count'],1);self.assertEqual(len(state['plans']),1)
            self.assertIsNone(self.runner.report()['metrics'][p['class_name']]['net_pnl_model'])
        self.assertTrue(all(e['actual_send'] is False and e['provider_acknowledged'] is False
            for r in rows for e in r['events'] if e['kind']=='modeled_delivery'))

    def test_modeled_failure_retains_qualified_plan_but_no_position_or_cooldown(self):
        rows=self.delivery_case('modeled_failure')
        self.assertTrue(all(r['no_entry'] and r['plan_ids']==[] and len(r['qualified_frozen_plan_ids'])==1 for r in rows))
        for p in self.manifest['policies']:
            state=self.runner.state(p['class_name'])
            self.assertEqual(state['alert']['count'],0);self.assertEqual(state['plans'],[])
            self.assertEqual(self.runner.report()['metrics'][p['class_name']]['net_pnl_model'],0)


class PollInputTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.store=LiveEvidenceStore(Path(self.tmp.name)/'live.db');self.addCleanup(self.store.close)

    def append(self,kind,at,**data):
        return self.store.append(dict(version=VERSION,kind=kind,observed_at=at,data=data))

    def quote(self):
        return self.append('quote_frame',300.,quote=100.,normalized=dict(symbol='BTC/USDT:USDT',last=100.),
            requested_at='1970-01-01T00:04:59.123456Z',received_at='1970-01-01T00:05:00.000000Z')

    def poll(self,at=301.,quote_key=None,**more):
        return self.append('entry_poll',at,policy='capture',quote=100.,native_state=None,
            quote_record_hash=quote_key,journal_available=True,
            original_schedule_state=dict(last_sample=300.,poll_seconds=60,watch=None),**more)

    def test_exact_normalized_receipt_and_poll_bytes_export_no_timestamp_backfill(self):
        q=self.quote();p=self.poll(quote_key=q);rows=stored_poll_points(self.store.db)
        self.assertEqual(len(rows),1);row=rows[0]
        self.assertEqual(row['quote_record_hash'],q)
        self.assertEqual(row['source_observation']['entry_poll_record_hash'],p)
        self.assertEqual(row['source_observation']['normalized_quote_receipt_envelope']['data']['requested_at'],
                         '1970-01-01T00:04:59.123456Z')
        self.assertIsNone(row['received_snapshot_hash']);self.assertFalse(row['source_observation']['receipt_continuity_verified'])
        decisions=[json.loads(r[0]) for r in self.store.db.execute('SELECT payload FROM decision_events')]
        self.assertEqual(decisions[0]['state'],'entry_poll');self.assertFalse(decisions[0]['actual_gate_vector_available'])

    def test_missing_receipt_remains_null_and_future_wrong_receipt_rolls_back(self):
        self.poll();self.assertIsNone(stored_poll_points(self.store.db)[0]['source_observation']['normalized_quote_receipt_envelope'])
        with self.assertRaisesRegex(ValueError,'QUOTE_RECEIPT_RECORD_REQUIRED'):self.poll(302.,quote_key='absent')
        self.assertEqual(len(stored_poll_points(self.store.db)),1)

    def test_recorded_gap_is_not_interpolated_into_no_entry_poll(self):
        self.append('gap',300.,reason='sidecar_loss',count=2)
        self.poll();row=stored_poll_points(self.store.db)[0]
        self.assertEqual(len(row['source_observation']['sidecar_gap_records']),1)
        self.assertEqual(row['source_observation']['sidecar_gap_records'][0]['data']['count'],2)
        self.assertEqual(len(stored_poll_points(self.store.db)),1)

    def test_bounded_export_cursor_is_stable_after_new_future_records(self):
        self.poll();first=stored_poll_points(self.store.db,limit=1)[0]
        self.append('gap',302.,reason='sidecar_loss',count=1);self.poll(303.)
        self.assertEqual(first,stored_poll_points(self.store.db,limit=1)[0])
        rest=stored_poll_points(self.store.db,after_rowid=first['sidecar_rowid'])
        self.assertEqual(len(rest),1);self.assertEqual(rest[0]['observed_at'],303.)
        for value in (0,10001,True):
            with self.assertRaises(ValueError):stored_poll_points(self.store.db,limit=value)

    def test_disabled_and_failed_poll_recording_cannot_escape_into_monitor(self):
        self.assertIsNone(observe(DisabledEvidence(),'entry_poll',at=300.))
        class Broken:
            enabled=True
            def emit(self,*args,**kwargs):raise RuntimeError('fixture')
        self.assertIsNone(observe(Broken(),'entry_poll',at=300.))

    def test_monitor_records_each_poll_before_due_checks_without_wrapping_strategy_call(self):
        tree=ast.parse(Path(replay.monitor.__file__).read_text())
        run=next(n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name=='run')
        body=next(n for n in run.body if isinstance(n,ast.While)).body[0].body
        i=next(i for i,n in enumerate(body) if isinstance(n,ast.Assign) and ast.unparse(n.targets[0])=='sample_due')
        call=body[i-1]
        self.assertIsInstance(call,ast.Expr);self.assertEqual(ast.unparse(call.value.func),'observe_evidence')
        self.assertEqual(call.value.args[1].value,'entry_poll')

if __name__=='__main__':unittest.main()
