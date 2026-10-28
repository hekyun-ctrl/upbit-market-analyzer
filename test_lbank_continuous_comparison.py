"""Restart/atomic policy-state orchestration with real frozen policy functions."""
import copy
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from lbank_signal_evidence import EvidenceLedger,canonical
from research.continuous_comparison import ContinuousComparison,declaration,policy_spec,initial_state
from research import compare_entry_refinement as replay
import test_lbank_native_entry as native_fixture


class ContinuousTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.bundles,cls.bars=replay.load_archives(replay.DATA/'archive_chunks.json')
        cls.costs=cls.bundles[0]['model_config']

    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.path=Path(self.tmp.name)/'book.sqlite3';self.book=EvidenceLedger(self.path);self.addCleanup(lambda:self.book.close())
        self.manifest=declaration([policy_spec(0),policy_spec(2)],self.costs)
        self.runner=ContinuousComparison(self.book,self.manifest)

    def point(self,seq=0):
        return dict(seq=seq,observed_at=300.+seq,quote=100.,native_state=None,
                    source_observation=dict(logged_at=replay.iso(300.+seq),price=100.))

    def test_unavailable_intervals_retained_and_economics_null_not_zero(self):
        rows=self.runner.step(self.point());self.assertEqual(len(rows),2)
        for row in self.runner.report()['metrics'].values():
            self.assertEqual(row['unavailable'],1);self.assertIsNone(row['net_pnl_model'])
            self.assertIsNone(row['unresolved_ratio']);self.assertFalse(row['comparison_economics_complete'])
        self.assertEqual(self.book.db.execute('SELECT count(*) FROM comparison_observations').fetchone()[0],1)

    def test_duplicate_observation_is_idempotent_and_revision_rejected(self):
        p=self.point();rows=self.runner.step(p)
        self.assertEqual(rows,self.runner.step(p))
        with self.assertRaisesRegex(ValueError,'OBSERVATION_REVISION'):self.runner.step(dict(p,quote=101.))
        self.assertTrue(all(s['observations']==1 for s in self.runner.report()['metrics'].values()))

    def test_missing_sequence_and_backward_time_rejected(self):
        self.runner.step(self.point())
        with self.assertRaisesRegex(ValueError,'SEQUENCE_GAP'):self.runner.step(self.point(2))
        with self.assertRaisesRegex(ValueError,'TIME_REGRESSION'):self.runner.step(dict(self.point(1),observed_at=299.))

    def test_second_policy_failure_rolls_back_first_state_and_source_cursor(self):
        original=self.runner._step_policy;calls=[]
        def fail(spec,*args):
            calls.append(1)
            if len(calls)==2:raise sqlite3.OperationalError('fixture')
            return original(spec,*args)
        before=self.runner.report()
        with patch.object(self.runner,'_step_policy',side_effect=fail):
            with self.assertRaises(sqlite3.OperationalError):self.runner.step(self.point())
        self.assertEqual(self.runner.report(),before)
        self.assertEqual(self.book.db.execute('SELECT count(*) FROM comparison_observations').fetchone()[0],0)

    def test_policy_watch_cooldown_and_sticky_invalidation_are_separate_and_survive_restart(self):
        names=[s['class_name'] for s in self.manifest['policies']]
        current=self.runner.state(names[0]);current.update(watch=dict(time=200.,plan=dict(price=100.)),invalidated_events=['fixture-op'])
        current['alert'].update(count=7,last_time=299.,last_side='롱')
        self.book.db.execute('UPDATE comparison_states SET payload=? WHERE run_id=? AND policy=?',(canonical(current),self.runner.identity,names[0]));self.book.db.commit()
        self.book.close();self.book=EvidenceLedger(self.path);self.runner=ContinuousComparison(self.book,self.manifest)
        self.assertEqual(self.runner.state(names[0]),current)
        self.assertEqual(self.runner.state(names[1]),initial_state())

    def test_manifest_code_costs_and_spec_are_frozen(self):
        bad=copy.deepcopy(self.manifest);bad['costs']['fee']=.001
        changed=ContinuousComparison(self.book,bad)
        self.assertNotEqual(changed.identity,self.runner.identity)
        self.runner.manifest['costs']['fee']=.001
        with self.assertRaisesRegex(ValueError,'FROZEN_RUN_DECLARATION_CHANGED'):self.runner.step(self.point())
        with self.assertRaisesRegex(ValueError,'DUPLICATE_POLICY'):declaration([policy_spec(0),policy_spec(0)],self.costs)
        with self.assertRaisesRegex(ValueError,'UNKNOWN_FROZEN_POLICY'):policy_spec(99)

    def test_cap_stops_without_deletion_or_partial_step(self):
        runner=ContinuousComparison(self.book,self.manifest,max_observations=1);runner.step(self.point())
        with self.assertRaisesRegex(ValueError,'CAP_NONDELETING'):runner.step(self.point(1))
        self.assertEqual(self.book.db.execute('SELECT count(*) FROM comparison_observations').fetchone()[0],1)

    def test_unseen_has_no_manifest_toggle_that_qualifies_missing_sources(self):
        manifest=declaration([policy_spec(0),policy_spec(2)],self.costs,purpose='unseen')
        runner=ContinuousComparison(self.book,manifest)
        point=self.point();point.update(received_snapshot_hash='synthetic-assertion',quote_record_hash='synthetic-assertion')
        rows=runner.step(point)
        self.assertTrue(all(r['status']=='input_unavailable' and not r['qualification']['qualified'] for r in rows))
        self.assertTrue(all(r['net_pnl_model'] is None for r in runner.report()['metrics'].values()))

    def test_frozen_policy_function_globals_restore_after_evaluation(self):
        fixture=native_fixture.NativeEntryTests();fixture.setUp();self.addCleanup(fixture.doCleanups);state,_=fixture.snapshot()
        point=dict(self.point(),observed_at=fixture.now,quote=state['quote'],native_state=state)
        before={n:getattr(replay.monitor,n) for n in ('classify','assess_candidate','confirm_watch','calculate_plan','alert_gate','ENTRY_REFINEMENT_MODE','TAKER_FEE')}
        with patch.object(replay.monitor,'telegram',side_effect=AssertionError('SEND_FORBIDDEN')):self.runner.step(point)
        for n,v in before.items():self.assertIs(getattr(replay.monitor,n),v,n)
        for s in self.runner.report()['metrics'].values():
            self.assertEqual(s['valid_observations'],1);self.assertEqual(s['trade_count'],0)
            self.assertEqual(s['net_pnl_model'],0);self.assertIsNone(s['unresolved_ratio'])

    def test_future_receipt_is_rejected_before_any_policy_runs(self):
        point=self.point();point['native_state']=dict(quote=100.,minute_bars=[dict(start=240,received_at=301)])
        with self.assertRaisesRegex(ValueError,'FUTURE_COMPARISON_SOURCE'):self.runner.step(point)
        self.assertEqual(self.book.db.execute('SELECT count(*) FROM comparison_observations').fetchone()[0],0)

if __name__=='__main__':unittest.main()
