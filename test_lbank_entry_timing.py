import copy
import json
import os
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

import lbank_futures_monitor as monitor
from lbank_entry_timing import completed_key, setup_evidence, confirmation_evidence, POLICY
from lbank_entry_replay_audit import capture, replay, readonly
from lbank_performance import PerformanceBook
from test_lbank_entry_quality import structural_state
import test_lbank_native_entry as native_entry_tests


class EntryTimingTests(unittest.TestCase):
    def test_only_native_completed_bars_produce_new_bar_keys(self):
        state=structural_state()
        self.assertEqual(completed_key(state),(8940,8700))
        for key,value in [('source','ticker'),('minute_bars',[])]:
            broken=copy.deepcopy(state);broken[key]=value
            self.assertIsNone(completed_key(broken))
        state['minute_bars'][-1]['quality']='forming'
        self.assertIsNone(completed_key(state))

    def test_nominal_close_is_not_mislabeled_as_publication(self):
        e=setup_evidence(structural_state(),9015.,9017.,9015.,'new_completed_bar')
        self.assertEqual(e['policy'],POLICY)
        self.assertEqual(e['evaluation_after_nominal_close_seconds'],17.)
        self.assertEqual(e['evaluation_after_first_ready_observation_seconds'],2.)
        self.assertIsNone(e['source_publication_at'])

    def test_short_bounce_is_better_price_not_positive_fresh_momentum(self):
        watch={'time':100.,'plan':{'price':81319.7}}
        p={'price':81369.8,'direction':-1,'entry_quality':{'minute_followthrough':False,
            'priority':{'dominant_intact':False},'ranked_entry':{'higher_direction_intact':False}}}
        e=confirmation_evidence(watch,p,None,130.,136.)
        self.assertTrue(e['entry_quote_improved_for_side'])
        self.assertAlmostEqual(e['side_adjusted_quote_change_pct'],-.0616086877)
        self.assertFalse(e['minute_followthrough']);self.assertFalse(e['dominant_intact'])
        self.assertFalse(e['profitability_verified']);self.assertEqual(e['watch_age_at_confirmation_seconds'],30.)

    def test_signal_journal_preserves_timing_without_new_account_or_order_data(self):
        book=PerformanceBook(':memory:',1000);self.addCleanup(book.db.close)
        p=dict(direction=1,price=80000,stop=79800,target1=80600,target2=81000,
               quantity_btc=.01,net_loss=3,entry_timing={'policy':POLICY})
        book.add(1,p,9000.)
        saved=json.loads(book.db.execute("SELECT plan FROM signals LIMIT 1").fetchone()[0])
        self.assertEqual(saved['entry_timing'],{'policy':POLICY})

    def test_capture_does_not_create_missing_database(self):
        with tempfile.TemporaryDirectory() as directory:
            absent=os.path.join(directory,'absent.sqlite3')
            with self.assertRaises(sqlite3.OperationalError):capture(absent,absent,9000.)
            self.assertFalse(os.path.exists(absent))

    def test_incident_capture_and_real_gates_reconstruct_recorded_signal(self):
        fixture=native_entry_tests.NativeEntryTests('test_native_watch_and_confirmation_never_call_sampled_gates')
        fixture.setUp();self.addCleanup(fixture.doCleanups)
        # An earlier real down-close prevents an artificial flat RSI=100, while
        # keeping the final native RSI direction positive. No gate is mocked.
        t=fixture.start+254*60
        with fixture.writer.db:
            fixture.writer.db.execute('UPDATE native_bars SET close=close-300,low=low-300 '
                                      'WHERE period=60 AND start=?',(t,))
            fixture.writer._aggregate(t//300*300)
        state,_=fixture.snapshot();watch=monitor.classify(state['prices'],native_state=state)
        self.assertIsNotNone(watch)
        confirmed=monitor.confirm_watch(state['prices'],watch,30.,native_state=state)
        self.assertIsNotNone(confirmed)
        path=os.path.join(fixture.directory.name,'performance.sqlite3')
        book=PerformanceBook(path,monitor.REF_BALANCE);self.addCleanup(book.db.close)
        book.sample(fixture.now,state['quote'],decision={'reason':'qualified'})
        book.sample(fixture.now+30,state['quote'])
        book.add(13,confirmed,fixture.now+30)
        bundle=capture(path,fixture.path,fixture.now+40)
        before=fixture.writer.db.execute('SELECT count(*) FROM native_bars').fetchone()[0]
        result=replay(bundle,monitor.classify,monitor.confirm_watch)
        self.assertTrue(result['actual_confirmation_replay']['accepted'])
        self.assertTrue(result['actual_confirmation_replay']['target1_matches'])
        self.assertEqual(result['first_qualified_recorded_quote']['at'],fixture.now)
        self.assertFalse(result['first_live_qualification_known'])
        self.assertFalse(result['pnl_computed'])
        self.assertEqual(fixture.writer.db.execute('SELECT count(*) FROM native_bars').fetchone()[0],before)
        with self.assertRaises(sqlite3.OperationalError):
            db=readonly(path)
            try:db.execute('DELETE FROM samples')
            finally:db.close()
        again=replay(bundle,monitor.classify,monitor.confirm_watch)
        self.assertEqual(result,again)

    def test_replay_never_expands_missing_minute_from_a_five_minute_bar(self):
        fixture=native_entry_tests.NativeEntryTests('test_native_watch_and_confirmation_never_call_sampled_gates')
        fixture.setUp();self.addCleanup(fixture.doCleanups)
        columns='period,start,open,high,low,close,volume,turnover,updated,quality'
        rows=[list(r) for r in fixture.writer.db.execute('SELECT '+columns+' FROM native_bars')]
        missing=fixture.now//60*60-60
        rows=[r for r in rows if not(r[0]==60 and r[1]==missing)]
        bundle=dict(signal=dict(id='test:13',opened=fixture.now,plan=dict(
            price=87500.,direction=1,strategy='breakout',entry_quality={})),
            samples=[[fixture.now,87500.]],decisions=[],native_rows=rows)
        result=replay(bundle,monitor.classify,monitor.confirm_watch)
        self.assertIsNone(result['first_qualified_recorded_quote'])
        self.assertFalse(result['points'][0]['native_ready'])


if __name__=='__main__':unittest.main()
