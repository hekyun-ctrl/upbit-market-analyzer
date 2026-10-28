"""Optional, failure-isolated evidence sidecar. It never sends or changes gates.

Only the worker opens SQLite. Producers make a bounded JSON snapshot and use a
nonwaiting queue. Queue/full/disk losses are explicit gaps, never no-entry rows.
This is observational instrumentation, not a durable outbox or paper engine.
"""
import hashlib
import json
import logging
import math
import os
from pathlib import Path
import queue
import sqlite3
import threading
import time
import uuid

from lbank_signal_evidence import EvidenceLedger, canonical, digest, gate_vector

LOG = logging.getLogger('lbank_live_evidence')
VERSION = 2
KINDS = frozenset(('native_frame', 'observation', 'candidate', 'confirmation', 'alert_eligibility',
                   'frozen_plan', 'delivery', 'outcome', 'gap', 'signal_link', 'followup'))
BLOCKERS = ['RAW5M_INDEPENDENCE_UNVERIFIED', 'CANDLE_VOLUME_UNIT_UNVERIFIED']


def anchor_for(plan, state):
    """Exact source occurrence using the P0 anchor rule; absent stays unmatched."""
    if not plan or not state:
        return None, None
    direction = plan['direction']
    family = plan.get('strategy', 'breakout')
    bars = state.get('minute_bars', [])
    proof = plan.get('entry_location')
    if proof:
        field = 'high' if direction == 1 else 'low'
        level, family = proof['trigger'], 'breakout_level'
        rows = [b for b in bars if b['start'] < proof['pattern_start']][-10:]
    elif family == 'breakout':
        field = 'high' if direction == 1 else 'low'
        level, family, rows = plan.get('level'), 'breakout_level', bars[-16:-1]
    else:
        field = 'low' if direction == 1 else 'high'
        level, rows = plan.get('structure_anchor'), bars[-6:-1]
    found = [b for b in rows if level is not None and abs(b[field] - level) < 1e-7]
    if not found:
        return None, family
    bar = max(found, key=lambda b: b['start'])
    return dict(bar_hash=digest(bar), bar_start=bar['start'], period=60,
                field=field, price=level, known_at=None), family


class LiveEvidenceStore:
    """Additive v0/v1 -> v2 migration; legacy evidence payloads stay untouched."""
    def __init__(self, path, *, max_bytes=64*1024*1024, max_records=100000):
        self.path = Path(path)
        self.max_bytes, self.max_records = max_bytes, max_records
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.book = EvidenceLedger(path, timeout=0.05)
        self.db = self.book.db
        try:
            version = self.db.execute('PRAGMA user_version').fetchone()[0]
            if version not in (0, 1, VERSION):
                raise ValueError('EVIDENCE_DATABASE_VERSION_UNSUPPORTED')
            # DDL is transactional (not executescript, which implicitly commits).
            with self.db:
                self.db.execute('CREATE TABLE IF NOT EXISTS live_records('
                    'record_hash TEXT PRIMARY KEY, kind TEXT NOT NULL, observed_at REAL NOT NULL,'
                    'plan_id TEXT, payload TEXT NOT NULL)')
                self.db.execute('CREATE TABLE IF NOT EXISTS live_sessions('
                    'session_id TEXT PRIMARY KEY, started_at REAL NOT NULL, ended_at REAL)')
                self.db.execute('CREATE TABLE IF NOT EXISTS live_audit('
                    'audit_hash TEXT PRIMARY KEY, payload TEXT NOT NULL)')
                self.db.execute('CREATE TABLE IF NOT EXISTS live_signal_links('
                    'reference INTEGER NOT NULL, sent_at REAL NOT NULL, '
                    'frozen_record_hash TEXT NOT NULL REFERENCES live_records(record_hash), '
                    'PRIMARY KEY(reference,sent_at))')
                self.db.execute('PRAGMA user_version=2')
            pages = self.db.execute('PRAGMA page_count').fetchone()[0]
            page_size = self.db.execute('PRAGMA page_size').fetchone()[0]
            if pages * page_size > max_bytes:
                raise ValueError('EXISTING_EVIDENCE_EXCEEDS_CAP')
            self.db.execute('PRAGMA max_page_count=' + str(max_bytes // page_size))
            self.db.execute('PRAGMA wal_autocheckpoint=1')
            self.db.execute('PRAGMA journal_size_limit=0')
        except Exception:
            self.book.close()
            raise

    def session_start(self, session):
        # A prior open session has unknown loss extent. Never infer zero losses.
        self._check_wal_cap()
        old = self.db.execute('SELECT session_id FROM live_sessions WHERE ended_at IS NULL').fetchall()
        with self.db:
            if old:
                value = dict(kind='unclean_restart', previous_sessions=[x[0] for x in old],
                             lost_records=None, receipt_continuity_verified=False)
                self.db.execute('INSERT OR IGNORE INTO live_audit VALUES(?,?)', (digest(value), canonical(value)))
            self.db.execute('INSERT INTO live_sessions VALUES(?,?,NULL)', (session, time.time()))

    def session_end(self, session):
        with self.db:
            self.db.execute('UPDATE live_sessions SET ended_at=? WHERE session_id=?', (time.time(), session))

    def append(self, envelope):
        if (envelope.get('version') != VERSION or envelope.get('kind') not in KINDS
                or type(envelope.get('observed_at')) not in (int, float)
                or not math.isfinite(envelope['observed_at']) or envelope['observed_at'] < 0):
            raise ValueError('INVALID_EVIDENCE_ENVELOPE')
        key = digest(envelope)
        if self.db.execute('SELECT 1 FROM live_records WHERE record_hash=?', (key,)).fetchone():
            return key
        if self.db.execute('SELECT count(*) FROM live_records').fetchone()[0] >= self.max_records:
            raise RuntimeError('EVIDENCE_RECORD_CAP_REACHED')
        # A pinned WAL reader can prevent truncation. Stop new writes rather than
        # let an analysis reader cause unlimited sidecar disk growth.
        self._check_wal_cap()
        with self.book.transaction():
            result = self._apply(envelope, key)
        self.db.execute('PRAGMA wal_checkpoint(TRUNCATE)')
        return result

    def _check_wal_cap(self):
        self.db.execute('PRAGMA wal_checkpoint(TRUNCATE)')
        wal = Path(str(self.path) + '-wal')
        if self.path.stat().st_size + (wal.stat().st_size if wal.exists() else 0) > self.max_bytes:
            raise RuntimeError('EVIDENCE_WAL_CAP_REACHED')

    def _apply(self, envelope, key):
        kind, data, at = envelope['kind'], envelope['data'], envelope['observed_at']
        plan_id = None
        if kind == 'native_frame':
            # Same wire frame can arrive repeatedly. Receipt versions are not
            # overwritten, and raw frame hash is distinct from occurrence hash.
            wire_hex, wire_sha256 = data.get('wire_hex'), data.get('wire_sha256')
            if wire_hex is not None and hashlib.sha256(bytes.fromhex(wire_hex)).hexdigest() != wire_sha256:
                raise ValueError('RAW_WIRE_HASH_MISMATCH')
            self.book.source(dict(frame=data['frame'], wire_hash=digest(data['frame']),
                                  raw_wire_hex=wire_hex, raw_wire_sha256=wire_sha256,
                                  exact_wire_bytes_available=wire_hex is not None,
                                  transport_received_at=data['received_at']),
                event_time=data.get('event_time'), received_at=data['received_at'],
                quality='raw_chart_feed; final_and_volume_and_independent_5m_unverified')
        elif kind in ('observation', 'candidate', 'confirmation', 'alert_eligibility', 'frozen_plan'):
            state = data.get('native_state')
            plan = data.get('plan')
            for period, name in ((60, 'minute_bars'), (300, 'five_bars')):
                for bar in (state or {}).get(name, []):
                    if bar['start'] + period > at or (bar.get('received_at') is not None
                                                      and bar['received_at'] > at):
                        raise ValueError('INPUT_NOT_AVAILABLE_AT_EVALUATION')
            anchor, family = anchor_for(plan, state) if kind == 'candidate' else (None, None)
            first_observed_at = at
            anchor_link_status = 'candidate_source' if anchor else 'anchor_not_available'
            if kind in ('confirmation', 'alert_eligibility', 'frozen_plan'):
                prior = self.db.execute("SELECT payload FROM live_records WHERE record_hash=? AND kind='candidate'",
                    (data.get('candidate_record_hash'),)).fetchone()
                if prior:
                    original = json.loads(prior[0])
                    first_observed_at = original['observed_at']
                    anchor, family = anchor_for(original['data'].get('plan'), original['data'].get('native_state'))
                    anchor_link_status = 'immutable_candidate_source' if anchor else 'candidate_anchor_missing'
                else:
                    anchor, family = None, None
                    anchor_link_status = 'candidate_record_missing'
            event = (self.book.opportunity(plan['direction'], family, anchor,
                         first_observed_at=first_observed_at) if anchor else None)
            # Snapshot observation is not a raw candle receipt. Never fill in
            # received_at/known_at from monitor evaluation time.
            raw = self.book.source(dict(kind='monitor_snapshot', at=at, state=state,
                                        quote=data.get('quote')),
                                   event_time=at, received_at=None,
                                   quality='evaluation_snapshot; raw_receipt_unknown')
            inputs = self.book.inputs([raw])
            self.book.decision(event=event, policy=data['policy'], evaluated_at=at,
                input_hash=inputs, state=kind, gates=gate_vector(),
                first_reject=data.get('reason'), available_at=None, availability_basis=None,
                diagnostics=data.get('diagnostics'), source_blockers=BLOCKERS,
                actual_gate_vector_available=False, candidate_is_entry=False,
                anchor_link_status=anchor_link_status)
            if kind == 'frozen_plan':
                plan_id = self.book.freeze(event=event, policy=data['policy'], plan=plan,
                    provenance=dict(input_hash=inputs, anchor=anchor, candidate_at=data.get('candidate_at'),
                                    confirm_at=at, live_record_hash=key, anchor_link_status=anchor_link_status,
                                    actual_fill_verified=False), intent=True)
                # Store a model-unresolved lifecycle immediately. No-entry is not
                # represented by a synthetic zero-return trade.
                self.book.outcome(plan_id, dict(status='unresolved', net_pnl_model=None,
                    reason='followup_not_yet_observed', actual_fill_verified=False))
        elif kind == 'delivery':
            row = self.db.execute('SELECT plan_id FROM live_records WHERE record_hash=?',
                                  (data['frozen_record_hash'],)).fetchone()
            if not row or not row[0]:
                raise ValueError('FROZEN_RECORD_REQUIRED')
            plan_id = row[0]
            self.book.delivery(plan_id, data['status'], **data.get('receipt', {}))
        elif kind == 'signal_link':
            row = self.db.execute('SELECT plan_id FROM live_records WHERE record_hash=?',
                                  (data['frozen_record_hash'],)).fetchone()
            if not row or not row[0]:
                raise ValueError('FROZEN_RECORD_REQUIRED')
            plan_id = row[0]
            prior = self.db.execute('SELECT frozen_record_hash FROM live_signal_links '
                'WHERE reference=? AND sent_at=?', (data['reference'], data['sent_at'])).fetchone()
            if prior and prior[0] != data['frozen_record_hash']:
                raise ValueError('SIGNAL_REFERENCE_REVISION')
            self.db.execute('INSERT OR IGNORE INTO live_signal_links VALUES(?,?,?)',
                            (data['reference'], data['sent_at'], data['frozen_record_hash']))
        elif kind == 'followup':
            row = self.db.execute('SELECT r.plan_id FROM live_signal_links l JOIN live_records r '
                'ON r.record_hash=l.frozen_record_hash WHERE l.reference=? AND l.sent_at=?',
                (data['reference'], data['sent_at'])).fetchone()
            if row and row[0]:
                plan_id = row[0]
                self.book.outcome(plan_id, data['value'])
            # Pre-instrumentation signals remain unlinked. The raw record is
            # retained, not attached to a reused display reference or made up plan.
        elif kind == 'outcome':
            row = self.db.execute('SELECT plan_id FROM live_records WHERE record_hash=?',
                                  (data['frozen_record_hash'],)).fetchone()
            if not row or not row[0]:
                raise ValueError('FROZEN_RECORD_REQUIRED')
            plan_id = row[0]
            self.book.outcome(plan_id, data['value'])
        with self.book.transaction():
            self.db.execute('INSERT INTO live_records VALUES(?,?,?,?,?)',
                            (key, kind, at, plan_id, canonical(envelope)))
        return key

    def archive(self, destination, *, comparison_reference):
        """Verified whole-store backup; does not authorize deletion or prune rows."""
        if not comparison_reference:
            raise ValueError('COMPARISON_REFERENCE_REQUIRED')
        destination = Path(destination)
        if destination.exists():
            raise ValueError('ARCHIVE_MUST_BE_NEW')
        target = sqlite3.connect(destination)
        try:
            self.db.backup(target)
            if target.execute('PRAGMA integrity_check').fetchone()[0] != 'ok':
                raise ValueError('ARCHIVE_INTEGRITY_FAILED')
            if target.execute('PRAGMA foreign_key_check').fetchall():
                raise ValueError('ARCHIVE_FOREIGN_KEY_FAILED')
        finally:
            target.close()
        sha = hashlib.sha256(destination.read_bytes()).hexdigest()
        audit = dict(schema='live_evidence_archive_v1', archive_sha256=sha,
                     archive_bytes=destination.stat().st_size, comparison_reference=comparison_reference,
                     records=self.db.execute('SELECT count(*) FROM live_records').fetchone()[0],
                     created_at=time.time(), deletion_performed=False)
        # Sidecar manifest remains usable when the database has hit its hard cap.
        manifest = destination.with_suffix(destination.suffix + '.json')
        with manifest.open('x') as stream:
            stream.write(canonical(audit) + '\n')
        return audit

    def close(self):
        self.book.close()


class DisabledEvidence:
    enabled = False
    def emit(self, *args, **kwargs):
        return None


class LiveEvidenceAdapter:
    enabled = True
    def __init__(self, path, *, capacity=64, max_event_bytes=512*1024,
                 max_bytes=64*1024*1024, max_records=100000, retry_seconds=1,
                 store_factory=LiveEvidenceStore):
        if capacity < 1 or max_event_bytes < 1 or max_bytes < 128*1024 or max_records < 1:
            raise ValueError('INVALID_EVIDENCE_BOUNDS')
        self.path, self.max_event_bytes = path, max_event_bytes
        self.options = dict(max_bytes=max_bytes, max_records=max_records)
        self.queue = queue.Queue(maxsize=capacity)
        self.store_factory, self.retry_seconds = store_factory, retry_seconds
        self.stop = threading.Event()
        self.lock = threading.Lock()
        self.session = uuid.uuid4().hex
        self.stats = dict(accepted=0, persisted=0, dropped=0, failed=0, pending_gap=0,
                          last_error=None, ready=False)
        self.thread = threading.Thread(target=self._run, name='lbank-evidence-sidecar', daemon=True)
        self.thread.start()

    def emit(self, kind, data, *, observed_at):
        try:
            if self.stop.is_set() or kind not in KINDS:
                raise ValueError('EVIDENCE_CLOSED_OR_UNKNOWN_KIND')
            envelope = dict(version=VERSION, kind=kind, observed_at=observed_at, data=data)
            payload = canonical(envelope)
            if len(payload.encode()) > self.max_event_bytes:
                raise ValueError('EVIDENCE_EVENT_TOO_LARGE')
            with self.lock:
                if self.stop.is_set():
                    raise ValueError('EVIDENCE_CLOSED')
                self.queue.put_nowait(payload)
                self.stats['accepted'] += 1
            return digest(envelope)
        except Exception as exc:
            with self.lock:
                self.stats['dropped'] += 1
                self.stats['pending_gap'] += 1
                self.stats['last_error'] = type(exc).__name__
            if self.stats['dropped'] == 1:
                LOG.warning('EVIDENCE_LOSS producer=true exception=%s risk_and_collector_unblocked=true', type(exc).__name__)
            return None

    def _run(self):
        store = None
        try:
            while not self.stop.is_set() or not self.queue.empty():
                if store is None:
                    try:
                        store = self.store_factory(self.path, **self.options)
                        store.session_start(self.session)
                        self.stats['ready'] = True
                    except Exception as exc:
                        if store:
                            store.close()
                        store = None
                        self.stats['failed'] += 1
                        self.stats['last_error'] = type(exc).__name__
                        if self.stats['failed'] == 1:
                            LOG.warning('EVIDENCE_UNAVAILABLE exception=%s risk_and_collector_unblocked=true', type(exc).__name__)
                        if self.stop.wait(self.retry_seconds):
                            return
                        continue
                try:
                    payload = self.queue.get(timeout=0.05)
                except queue.Empty:
                    continue
                try:
                    with self.lock:
                        lost = self.stats['pending_gap']
                    if lost:
                        store.append(dict(version=VERSION, kind='gap', observed_at=time.time(),
                            data=dict(reason='sidecar_loss', count=lost, session=self.session,
                                      receipt_continuity_verified=False)))
                        with self.lock:
                            self.stats['pending_gap'] -= lost
                    store.append(json.loads(payload))
                    self.stats['persisted'] += 1
                except Exception as exc:
                    # Optional analysis is lower priority than collector/risk.
                    # No retry/send side effect. The loss is recorded on recovery.
                    self.stats['failed'] += 1
                    with self.lock:
                        self.stats['pending_gap'] += 1
                        self.stats['last_error'] = type(exc).__name__
                    if self.stats['failed'] == 1:
                        LOG.warning('EVIDENCE_LOSS writer=true exception=%s risk_and_collector_unblocked=true', type(exc).__name__)
                finally:
                    self.queue.task_done()
            if store is not None and not self.stats['pending_gap']:
                store.session_end(self.session)
        finally:
            self.stats['ready'] = False
            if store:
                store.close()

    def close(self, timeout=0):
        # Live callers never wait for storage. Tests/admin may request a bound.
        with self.lock:
            self.stop.set()
        self.thread.join(timeout)
        return not self.thread.is_alive()


def from_environment():
    if os.getenv('LBANK_LIVE_EVIDENCE_ENABLED', 'false').lower() != 'true':
        return DisabledEvidence()
    try:
        return LiveEvidenceAdapter(os.getenv('LBANK_LIVE_EVIDENCE_DB', '/data/lbank_live_evidence.sqlite3'))
    except Exception as exc:
        LOG.error('EVIDENCE_DISABLED exception=%s', type(exc).__name__)
        return DisabledEvidence()


def observe(adapter, kind, *, at, **data):
    """Instrumentation errors cannot escape into the monitor or collector."""
    if not getattr(adapter, 'enabled', False):
        return None
    try:
        return adapter.emit(kind, data, observed_at=at)
    except Exception:
        return None


def delivery_state(delivered, transport_class):
    # Existing transport exposes bool, not the provider message ID. A boolean
    # is retained as evidence but cannot certify an ACK with an invented ID.
    return ('send_ack_unknown' if delivered or transport_class == 'network_or_response_error'
            else 'send_failed')


def capture_native_frame(adapter, frame, wire, received_at):
    if not getattr(adapter, 'enabled', False):
        return None
    try:
        raw = wire.encode('utf-8') if isinstance(wire, str) else bytes(wire)
        return observe(adapter, 'native_frame', at=received_at, frame=frame,
            received_at=received_at, event_time=frame.get('w', 0)/1000,
            wire_hex=raw.hex(), wire_sha256=hashlib.sha256(raw).hexdigest())
    except Exception:
        return None
