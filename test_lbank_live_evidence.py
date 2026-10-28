"""Sidecar failure injection and retained-real-data integration; no network/sends."""
import copy
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

from lbank_signal_evidence import EvidenceLedger, canonical, digest
from lbank_live_evidence import (LiveEvidenceStore, LiveEvidenceAdapter, DisabledEvidence,
    from_environment, observe, delivery_state, VERSION)


def envelope(kind='candidate', at=300., **data):
    base = dict(policy='capture', quote=100., plan=None, native_state=None)
    base.update(data)
    return dict(version=VERSION, kind=kind, observed_at=at, data=base)


def frozen():
    return envelope('frozen_plan', plan=dict(direction=1, strategy='trend_retest', price=100.,
                    stop=99., target1=102., target2=103.), candidate_at=240.)


def wait_for(predicate, timeout=3):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if predicate():
            return
        time.sleep(.005)
    raise AssertionError('sidecar did not reach expected test state')


class StoreTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / 'evidence.sqlite3'
        self.store = LiveEvidenceStore(self.path)
        self.addCleanup(lambda: self.store.close())

    def test_additive_migration_preserves_legacy_bytes_and_is_repeatable(self):
        self.store.close()
        old = EvidenceLedger(self.path)
        old.source(dict(price=101.), event_time=100., received_at=None)
        before = old.db.execute('SELECT * FROM source_events').fetchall()
        old.db.execute('PRAGMA user_version=1')
        old.close()
        self.store = LiveEvidenceStore(self.path)
        self.assertEqual(self.store.db.execute('SELECT * FROM source_events').fetchall(), before)
        self.assertEqual(self.store.db.execute('PRAGMA user_version').fetchone()[0], 2)
        self.store.close()
        self.store = LiveEvidenceStore(self.path)
        self.assertEqual(self.store.db.execute('SELECT * FROM source_events').fetchall(), before)

    def test_newer_database_is_rejected_without_mutation(self):
        other = Path(self.tmp.name) / 'future.sqlite3'
        db = sqlite3.connect(other)
        db.execute('PRAGMA user_version=99')
        db.close()
        before = hashlib.sha256(other.read_bytes()).hexdigest()
        with self.assertRaisesRegex(ValueError, 'VERSION_UNSUPPORTED'):
            LiveEvidenceStore(other)
        self.assertEqual(hashlib.sha256(other.read_bytes()).hexdigest(), before)

    def test_atomic_envelope_rolls_back_all_derived_rows_after_disk_failure(self):
        self.store.db.executescript("CREATE TRIGGER fail_record BEFORE INSERT ON live_records BEGIN SELECT RAISE(ABORT,'disk failure fixture'); END;")
        with self.assertRaises(sqlite3.IntegrityError):
            self.store.append(frozen())
        for table in ('source_events', 'input_sets', 'decision_events', 'frozen_plans', 'dispatch_events', 'outcome_events', 'live_records'):
            self.assertEqual(self.store.db.execute('SELECT count(*) FROM '+table).fetchone()[0], 0, table)
        self.store.db.execute('DROP TRIGGER fail_record')
        self.store.append(frozen())
        self.assertEqual(self.store.db.execute('SELECT count(*) FROM frozen_plans').fetchone()[0], 1)

    def test_restart_replay_is_idempotent_and_pending_delivery_stays_disabled(self):
        key = self.store.append(frozen())
        self.store.close()
        self.store = LiveEvidenceStore(self.path)
        self.assertEqual(self.store.append(frozen()), key)
        self.assertEqual(self.store.db.execute('SELECT count(*) FROM live_records').fetchone()[0], 1)
        self.assertEqual(self.store.db.execute('SELECT status FROM dispatch_events').fetchone()[0], 'DISABLED')
        self.assertIsNone(json.loads(self.store.db.execute('SELECT payload FROM outcome_events').fetchone()[0])['net_pnl_model'])

    def test_unclean_restart_explicit_unknown_gap_clean_restart_does_not_add_gap(self):
        self.store.session_start('first')
        self.store.session_start('second')
        audit = json.loads(self.store.db.execute('SELECT payload FROM live_audit').fetchone()[0])
        self.assertEqual(audit['previous_sessions'], ['first'])
        self.assertIsNone(audit['lost_records'])
        self.assertFalse(audit['receipt_continuity_verified'])
        self.store.session_end('first')
        self.store.session_end('second')
        self.store.session_start('clean')
        self.assertEqual(self.store.db.execute('SELECT count(*) FROM live_audit').fetchone()[0], 1)

    def test_candidate_is_not_entry_and_unknown_receipts_and_unrun_gates_stay_unknown(self):
        self.store.append(envelope(reason='market_no_setup'))
        self.assertEqual(self.store.db.execute('SELECT count(*) FROM frozen_plans').fetchone()[0], 0)
        self.assertEqual(self.store.db.execute('SELECT count(*) FROM outcome_events').fetchone()[0], 0)
        source = json.loads(self.store.db.execute('SELECT payload FROM source_events').fetchone()[0])
        decision = json.loads(self.store.db.execute('SELECT payload FROM decision_events').fetchone()[0])
        self.assertIsNone(source['received_at'])
        self.assertIsNone(decision['available_at'])
        self.assertIsNone(decision['availability_basis'])
        self.assertTrue(all(v['status']=='not_evaluated' for v in decision['gates'].values()))
        self.assertEqual(len(decision['source_blockers']), 2)

    def test_future_closed_bar_or_receipt_is_not_consumed(self):
        for bar in (dict(start=300), dict(start=240, received_at=301)):
            with self.assertRaisesRegex(ValueError, 'INPUT_NOT_AVAILABLE'):
                self.store.append(envelope(native_state=dict(minute_bars=[bar])))
        self.assertEqual(self.store.db.execute('SELECT count(*) FROM live_records').fetchone()[0], 0)

    def test_same_wire_frame_multiple_receipts_are_distinct_without_rewriting(self):
        frame = dict(w=100000, x=2)
        for receipt in (101., 102.):
            self.store.append(envelope('native_frame', at=receipt, frame=frame,
                received_at=receipt, event_time=100.))
        rows = [json.loads(r[0]) for r in self.store.db.execute('SELECT payload FROM source_events')]
        self.assertEqual(len(rows), 2)
        self.assertEqual({r['raw']['wire_hash'] for r in rows}, {digest(frame)})
        self.assertEqual({r['received_at'] for r in rows}, {101., 102.})

    def test_delivery_failure_unknown_ack_and_immutable_ack_history_survive_restart(self):
        key = self.store.append(frozen())
        for status in ('send_failed', 'send_ack_unknown'):
            self.store.append(envelope('delivery', frozen_record_hash=key, status=status, receipt={}))
        with self.assertRaisesRegex(ValueError, 'PROVIDER_ACK'):
            self.store.append(envelope('delivery', frozen_record_hash=key, status='acknowledged', receipt={}))
        self.store.append(envelope('delivery', frozen_record_hash=key, status='acknowledged',
                                  receipt=dict(provider_message_id='fixture-only')))
        self.store.close()
        self.store = LiveEvidenceStore(self.path)
        with self.assertRaisesRegex(ValueError, 'IMMUTABLE'):
            self.store.append(envelope('delivery', at=301., frozen_record_hash=key,
                                      status='send_failed', receipt={}))
        self.assertEqual(self.store.db.execute("SELECT count(*) FROM live_records WHERE kind='delivery'").fetchone()[0], 3)

    def test_reference_reuse_cannot_reassign_prior_plan_and_followup_is_not_fill(self):
        key = self.store.append(frozen())
        self.store.append(envelope('signal_link', reference=1, sent_at=300., frozen_record_hash=key))
        self.store.close()
        self.store = LiveEvidenceStore(self.path)
        value = dict(status='observed_followup', reason='target1_reached', net_pnl_model=None,
                     actual_fill_verified=False)
        self.store.append(envelope('followup', at=400., reference=1, sent_at=300., value=value))
        self.store.append(envelope('followup', at=401., reference=1, sent_at=999., value=value))
        rows = self.store.db.execute("SELECT plan_id FROM live_records WHERE kind='followup' ORDER BY observed_at").fetchall()
        self.assertIsNotNone(rows[0][0]); self.assertIsNone(rows[1][0])
        self.assertEqual(self.store.db.execute('SELECT count(*) FROM outcome_events').fetchone()[0], 2)

    def test_retention_record_cap_refuses_new_evidence_without_deletion_and_can_archive(self):
        self.store.max_records = 1
        self.store.append(frozen())
        with self.assertRaisesRegex(RuntimeError, 'RECORD_CAP'):
            self.store.append(envelope(at=301.))
        destination = Path(self.tmp.name) / 'reviewed.sqlite3'
        audit = self.store.archive(destination, comparison_reference='test-comparison-not-production')
        self.assertEqual(audit['archive_sha256'], hashlib.sha256(destination.read_bytes()).hexdigest())
        self.assertEqual(audit['records'], 1)
        self.assertFalse(audit['deletion_performed'])
        self.assertEqual(self.store.db.execute('SELECT count(*) FROM frozen_plans').fetchone()[0], 1)
        with self.assertRaisesRegex(ValueError, 'ARCHIVE_MUST_BE_NEW'):
            self.store.archive(destination, comparison_reference='test')

    def test_hard_sqlite_page_cap_and_failure_leave_existing_evidence_intact(self):
        self.store.append(frozen())
        before = self.store.db.execute('SELECT * FROM live_records').fetchall()
        pages = self.store.db.execute('PRAGMA page_count').fetchone()[0]
        self.store.db.execute('PRAGMA max_page_count='+str(pages))
        with self.assertRaises(sqlite3.OperationalError):
            self.store.append(envelope('gap', at=301., large='x'*200000))
        self.assertEqual(self.store.db.execute('SELECT * FROM live_records').fetchall(), before)
        self.assertEqual(self.store.db.execute('PRAGMA integrity_check').fetchone()[0], 'ok')

    def test_pinned_wal_reader_is_detected_before_further_unbounded_writes(self):
        self.store.append(envelope())
        reader = sqlite3.connect(self.path)
        self.addCleanup(reader.close)
        reader.execute('BEGIN'); reader.execute('SELECT * FROM live_records').fetchall()
        self.store.append(envelope(at=301.))
        total = self.path.stat().st_size + Path(str(self.path)+'-wal').stat().st_size
        self.store.max_bytes = total - 1
        with self.assertRaisesRegex(RuntimeError, 'WAL_CAP'):
            self.store.append(envelope(at=302.))
        self.assertEqual(self.store.db.execute('SELECT count(*) FROM live_records').fetchone()[0], 2)


class AdapterTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / 'live.sqlite3'

    def test_default_is_disabled_and_has_no_file_thread_or_sender(self):
        with patch.dict(os.environ, {}, clear=True):
            adapter = from_environment()
        self.assertIsInstance(adapter, DisabledEvidence)
        self.assertIsNone(observe(adapter, 'candidate', at=300., policy='capture'))
        self.assertFalse(self.path.exists())

    def test_blocked_worker_and_full_queue_do_not_block_real_collector_or_risk_check(self):
        from lbank_native_candles import NativeCandleStore
        from lbank_signal_risk import SignalRiskTracker
        blocked, release = threading.Event(), threading.Event()
        def factory(*args, **kwargs):
            blocked.set(); release.wait(3)
            return LiveEvidenceStore(*args, **kwargs)
        adapter = LiveEvidenceAdapter(self.path, capacity=1, store_factory=factory)
        self.addCleanup(lambda: (release.set(), adapter.close(2)))
        self.assertTrue(blocked.wait(1))
        observe(adapter, 'candidate', at=300., policy='capture', quote=100.)
        start = time.monotonic()
        self.assertIsNone(observe(adapter, 'candidate', at=301., policy='capture'))
        collector = NativeCandleStore(':memory:')
        self.addCleanup(collector.db.close)
        frame = dict(x=2, z=4, w=360000, d=dict(a='BTCUSDT', b='1m', c=360,
            k=360, d='100', f='101', g='99', e='100', h='1', i='100'))
        collector.ingest(frame, 1., wall_received=360.)
        tracker = SignalRiskTracker(path=str(Path(self.tmp.name)/'risk.json'))
        tracker.add_sent_signal(dict(direction=1, price=100., stop=99., target1=102.), 300.)
        result = tracker.check([100., 98.], 360.)
        self.assertTrue(result)
        self.assertLess(time.monotonic()-start, .5)
        self.assertEqual(adapter.queue.qsize(), 1)
        release.set()
        wait_for(lambda: adapter.stats['persisted']==1)
        self.assertTrue(adapter.close(2))
        book = LiveEvidenceStore(self.path); self.addCleanup(book.close)
        kinds = [r[0] for r in book.db.execute('SELECT kind FROM live_records')]
        self.assertIn('gap', kinds)

    def test_oversize_and_mutated_producer_payload_do_not_corrupt_queued_snapshot(self):
        blocked, release = threading.Event(), threading.Event()
        def factory(*args, **kwargs):
            blocked.set(); release.wait(3)
            return LiveEvidenceStore(*args, **kwargs)
        adapter = LiveEvidenceAdapter(self.path, max_event_bytes=1000, store_factory=factory)
        self.addCleanup(lambda: (release.set(), adapter.close(2)))
        self.assertTrue(blocked.wait(1))
        original = dict(policy='capture', quote=100.)
        self.assertIsNotNone(adapter.emit('candidate', original, observed_at=300.))
        original['quote'] = 999.
        self.assertIsNone(adapter.emit('gap', dict(raw='x'*2000), observed_at=300.))
        release.set(); wait_for(lambda: adapter.stats['persisted']==1)
        self.assertTrue(adapter.close(2))
        db = sqlite3.connect(self.path); self.addCleanup(db.close)
        row = json.loads(db.execute("SELECT payload FROM live_records WHERE kind='candidate'").fetchone()[0])
        self.assertEqual(row['data']['quote'], 100.)

    def test_startup_outage_recovers_without_producer_storage_wait(self):
        attempts = []
        def factory(*args, **kwargs):
            attempts.append(1)
            if len(attempts) == 1:
                raise sqlite3.OperationalError('fixture read-only volume')
            return LiveEvidenceStore(*args, **kwargs)
        adapter = LiveEvidenceAdapter(self.path, retry_seconds=.01, store_factory=factory)
        self.addCleanup(lambda: adapter.close(2))
        observe(adapter, 'candidate', at=300., policy='capture', quote=100.)
        wait_for(lambda: adapter.stats['persisted']==1)
        self.assertEqual(adapter.stats['failed'], 1)
        self.assertEqual(adapter.stats['dropped'], 0)

    def test_write_failure_recovers_with_gap_and_no_resend(self):
        class FailOnce(LiveEvidenceStore):
            once = True
            def append(self, event):
                if self.once:
                    self.once = False
                    raise sqlite3.OperationalError('fixture disk outage')
                return super().append(event)
        adapter = LiveEvidenceAdapter(self.path, store_factory=FailOnce)
        self.addCleanup(lambda: adapter.close(2))
        observe(adapter, 'candidate', at=300., policy='capture', quote=100.)
        wait_for(lambda: adapter.stats['failed']==1)
        observe(adapter, 'candidate', at=301., policy='capture', quote=101.)
        wait_for(lambda: adapter.stats['persisted']==1)
        self.assertTrue(adapter.close(2))
        db = sqlite3.connect(self.path); self.addCleanup(db.close)
        rows = db.execute('SELECT kind FROM live_records').fetchall()
        self.assertEqual(rows, [('gap',), ('candidate',)])
        self.assertEqual(db.execute('SELECT count(*) FROM frozen_plans').fetchone()[0], 0)

    def test_external_instrumentation_exception_never_escapes_and_boolean_ack_is_not_fabricated(self):
        class Broken:
            enabled = True
            def emit(self, *args, **kwargs):
                raise OSError('fixture')
        self.assertIsNone(observe(Broken(), 'candidate', at=300.))
        self.assertEqual(delivery_state(True, 'success'), 'send_ack_unknown')
        self.assertEqual(delivery_state(False, 'network_or_response_error'), 'send_ack_unknown')
        self.assertEqual(delivery_state(False, 'chat_forbidden_or_bot_blocked'), 'send_failed')


class RetainedDataTests(unittest.TestCase):
    def test_retained_raw_frames_keep_actual_receipts_without_upgrading_source_quality(self):
        from lbank_native_candles import decode_frame
        from datetime import datetime
        root = Path(__file__).resolve().parent
        audit = json.loads((root/'research/zerobase/completion_20261028/source_probe/audit.json').read_text())
        count = 0
        with tempfile.TemporaryDirectory() as directory:
            store = LiveEvidenceStore(Path(directory)/'live.sqlite3')
            try:
                for attempt in audit['websocket']:
                    for retained in attempt['frames']:
                        raw = bytes.fromhex(retained['hex'])
                        self.assertEqual(hashlib.sha256(raw).hexdigest(), retained['sha256'])
                        frame = decode_frame(raw)
                        receipt = datetime.fromisoformat(retained['received_at']).timestamp()
                        store.append(envelope('native_frame', at=receipt, frame=frame,
                            received_at=receipt, event_time=frame.get('w', 0)/1000))
                        count += 1
                self.assertEqual(count, 6)
                rows = [json.loads(r[0]) for r in store.db.execute('SELECT payload FROM source_events')]
                self.assertTrue(all(r['received_at'] is not None for r in rows))
                self.assertTrue(all('unverified' in r['quality'] for r in rows))
                self.assertEqual(store.db.execute('SELECT count(*) FROM frozen_plans').fetchone()[0], 0)
            finally:
                store.close()

    def test_retained_real_policy_inputs_outputs_unchanged_by_healthy_and_failed_sidecar(self):
        # Twelve preselected observations: first six available states plus six
        # exact stored candidate timestamps; this is an integration regression,
        # not another strategy comparison, holdout, or profitability assessment.
        from research import compare_entry_refinement as replay
        bundles, bars = replay.load_archives(replay.DATA/'archive_chunks.json')
        points = json.loads((replay.DATA/'recorded_quotes.json').read_text())
        saved = json.loads((replay.ROOT/'research/zerobase/entry_location_20261025/comparison.json').read_text())
        candidate_times = {p['at'] for w in saved['experiments']['capture_delay0']['windows'].values()
                           for p in w['opportunities']}
        with tempfile.TemporaryDirectory() as directory:
            feed = replay.make_feed(directory, bars)
            chosen = []
            for point in points:
                ts = replay.timestamp(point['logged_at'])
                state, reason = replay.available_state(feed, ts, point['price'], 0)
                if state and (len(chosen)<6 or replay.iso(ts) in candidate_times):
                    chosen.append((point, state, ts))
                if len(chosen)>=12:
                    break
            self.assertEqual(len(chosen), 12)
            store = LiveEvidenceStore(Path(directory)/'sidecar.sqlite3')
            config = {replay.CONFIG_MAP[k]:v for k,v in bundles[0]['model_config'].items()}
            config['ENTRY_REFINEMENT_MODE'] = 'capture'
            try:
                with patch.multiple(replay.monitor, **config), patch.object(replay.monitor, 'telegram', side_effect=AssertionError('SEND_FORBIDDEN')):
                    for point, state, ts in chosen:
                        original = copy.deepcopy(state)
                        diag1 = {}; plan1 = replay.monitor.classify(state['prices'], native_state=state, diagnostics=diag1)
                        store.append(envelope(at=ts, quote=point['price'], native_state=state,
                                              plan=plan1, diagnostics=diag1, reason=diag1.get('reason')))
                        diag2 = {}; plan2 = replay.monitor.classify(state['prices'], native_state=state, diagnostics=diag2)
                        self.assertEqual((plan1, diag1), (plan2, diag2))
                        self.assertEqual(state, original)
                    self.assertEqual(store.db.execute('SELECT count(*) FROM live_records').fetchone()[0], 12)
                    self.assertEqual(store.db.execute('SELECT count(*) FROM frozen_plans').fetchone()[0], 0)
            finally:
                store.close()


if __name__ == '__main__':
    unittest.main()
