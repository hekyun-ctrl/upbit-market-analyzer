"""Synthetic protocol mechanics and retained actual bytes; no market qualification."""
import copy
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest

from lbank_live_evidence import LiveEvidenceStore, VERSION
from lbank_received_stream import (ReceivedStreamImporter, seal_segment, strict_json,
    SCHEMA, CHART_SCHEMA, BAR_FIELDS, MAX_BYTES, receipt_time)
from lbank_signal_evidence import canonical, digest


def timestamp(at):
    return datetime.fromtimestamp(at, timezone.utc).isoformat(timespec='microseconds')


def bar(start=60, close=100.):
    return dict(period=60, start=start, open=100., high=102., low=98., close=close,
                volume=1., turnover=100., updated=start+59)


def synthetic_decoder(wire):
    # This adapter exists ONLY in the test file. No exchange final semantics are
    # inferred, and the importer always returns qualified=False even here.
    return strict_json(wire)


def wire(seq=1, at=121., bars=None, session='a'):
    raw = canonical(bars or [dict(bar=bar(), final=True, final_basis='synthetic_test_only')]).encode()
    return dict(kind='wire', seq=seq, session=session, schema='synthetic_test_only',
                received_at=timestamp(at), wire_hex=raw.hex(),
                wire_sha256=hashlib.sha256(raw).hexdigest())


def segment(rows, name='one', previous=None):
    return seal_segment(dict(schema=SCHEMA, stream='test', segment=name,
                             previous=previous, records=rows))


def snapshot(seq=2, at=122., bars=None, refs=None, session='a'):
    return dict(kind='snapshot', seq=seq, session=session, evaluated_at=timestamp(at),
                native_state=dict(minute_bars=bars if bars is not None else [bar()], five_bars=[]),
                quote=100., bar_refs=dict(minute_bars=refs if refs is not None else [dict(seq=1,index=0)], five_bars=[]))


class ReceivedTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name)/'live.sqlite3'
        self.store = LiveEvidenceStore(self.path)
        self.addCleanup(lambda:self.store.close())
        self.importer = ReceivedStreamImporter(self.store, decoders={'synthetic_test_only': synthetic_decoder})

    def count(self, table):
        return self.store.db.execute('SELECT count(*) FROM '+table).fetchone()[0]

    def rejects_atomic(self, rows, reason):
        before = {t:self.count(t) for t in ('received_segments','received_records','received_bars','received_snapshots','source_events')}
        with self.assertRaisesRegex((ValueError, sqlite3.Error), reason):
            self.importer.import_segment(segment(rows))
        self.assertEqual({t:self.count(t) for t in before}, before)

    def test_sealed_final_receipt_binds_exact_snapshot_and_stays_unqualified(self):
        result = self.importer.import_segment(segment([wire(), snapshot()]))
        snap = self.importer.snapshots()[0]
        self.assertFalse(result['qualified']);self.assertFalse(snap['qualified'])
        self.assertEqual(snap['available_at'],121.)
        self.assertEqual(snap['links'][0]['received_at'],timestamp(121.))
        self.assertEqual(snap['links'][0]['revision'],1)
        self.assertEqual(snap['links'][0]['snapshot_bar_hash'],digest(bar()))
        self.assertEqual(len(snap['source_blockers']),2)
        self.assertFalse(snap['receipt_continuity_verified'])

    def test_duplicate_segment_restart_idempotence_preserves_exact_bytes(self):
        data=segment([wire(),snapshot()]);self.importer.import_segment(data)
        before=self.store.db.execute('SELECT * FROM received_records').fetchall()
        self.store.close();self.store=LiveEvidenceStore(self.path)
        self.importer=ReceivedStreamImporter(self.store,decoders={'synthetic_test_only':synthetic_decoder})
        self.assertTrue(self.importer.import_segment(data)['duplicate'])
        self.assertEqual(self.store.db.execute('SELECT * FROM received_records').fetchall(),before)
        self.assertEqual(self.count('received_snapshots'),1)

    def test_identical_wire_same_receipt_occurrences_not_deduplicated(self):
        a=wire();b=copy.deepcopy(a);b['seq']=2
        self.importer.import_segment(segment([a,b,snapshot(seq=3,refs=[dict(seq=2,index=0)])]))
        self.assertEqual(self.count('source_events'),2)
        self.assertEqual([r[0] for r in self.store.db.execute('SELECT revision FROM received_bars ORDER BY seq')],[1,1])
        self.assertEqual(self.importer.snapshots()[0]['links'][0]['occurrence_seq'],2)

    def test_revisions_before_snapshot_require_latest_exact_occurrence(self):
        second=wire(seq=2,at=121.5,bars=[dict(bar=bar(close=101.),final=True,final_basis='synthetic_test_only')])
        self.rejects_atomic([wire(),second,snapshot(seq=3)],'STALE_BAR_REVISION')
        self.importer.import_segment(segment([wire(),second,snapshot(seq=3,bars=[bar(close=101.)],refs=[dict(seq=2,index=0)])]))
        self.assertEqual(self.importer.snapshots()[0]['links'][0]['revision'],2)

    def test_later_revision_does_not_rewrite_prior_snapshot(self):
        first=segment([wire(),snapshot()]);self.importer.import_segment(first)
        before=self.importer.snapshots()
        self.importer.import_segment(segment([wire(seq=3,at=123.,bars=[dict(bar=bar(close=101.),final=True,final_basis='synthetic_test_only')])],name='two',previous=first['sha256']))
        self.assertEqual(self.importer.snapshots(),before)
        self.assertEqual(self.count('received_bars'),2)

    def test_missing_unknown_false_final_never_binds(self):
        for final in (None,False,'true',1):
            with self.subTest(final=final):
                self.rejects_atomic([wire(bars=[dict(bar=bar(),final=final,final_basis=None)]),snapshot()],
                    'FINAL_RECEIPT_UNVERIFIED|FINAL_STATUS_REQUIRED')

    def test_final_without_documented_definition_rejected(self):
        self.rejects_atomic([wire(bars=[dict(bar=bar(),final=True,final_basis=None)])],'FINAL_DEFINITION_REQUIRED')

    def test_cannot_turn_builtin_chart_final_with_adapter_override(self):
        with self.assertRaisesRegex(ValueError,'CANNOT_BE_REPLACED'):
            ReceivedStreamImporter(self.store,decoders={CHART_SCHEMA:synthetic_decoder})

    def test_unknown_schema_and_extra_fields_fail_closed(self):
        a=wire();a['schema']='official_maybe';self.rejects_atomic([a],'UNKNOWN_NATIVE_WIRE_SCHEMA')
        a=wire();a['verified_final']=True;self.rejects_atomic([a],'UNKNOWN_RECEIVED_RECORD_SCHEMA')
        data=segment([wire()]);data['payload']['schema']='v99';data=seal_segment(data['payload'])
        with self.assertRaisesRegex(ValueError,'UNKNOWN_SEALED_STREAM_SCHEMA'):
            self.importer.import_segment(data)

    def test_missing_naive_or_nominal_receive_time_not_fabricated(self):
        for at in (None,121.,'1970-01-01T00:02:01','1970-01-01T01:02:01+01:00','1970-01-01T00:02:01.1234567Z'):
            with self.subTest(at=at):
                a=wire();a['received_at']=at
                self.rejects_atomic([a],'EXACT_UTC_TRANSPORT_TIME_REQUIRED')

    def test_future_final_and_bar_update_rejected(self):
        self.rejects_atomic([wire(at=119.5)],'BAR_NOT_AVAILABLE_AT_RECEIPT')
        a=bar();a['updated']=119
        self.rejects_atomic([wire(at=118.,bars=[dict(bar=a,final=False,final_basis=None)])],'BAR_NOT_AVAILABLE_AT_RECEIPT')

    def test_hash_tamper_and_strict_json_rejected(self):
        a=wire();a['wire_sha256']='0'*64;self.rejects_atomic([a],'RAW_WIRE_HASH_MISMATCH')
        data=segment([wire()]);data['payload']['stream']='changed'
        with self.assertRaisesRegex(ValueError,'HASH_MISMATCH'):self.importer.import_segment(data)
        for raw in ('{"a":1,"a":2}','{"a":NaN}'):
            with self.assertRaises(ValueError):strict_json(raw)

    def test_stream_chain_sequence_revision_conflict_and_receipt_clock(self):
        self.rejects_atomic([wire(seq=2)],'SEQUENCE_OR_SESSION')
        self.rejects_atomic([wire(),wire(seq=2,at=120.5)],'CLOCK_REGRESSION')
        first=segment([wire()]);self.importer.import_segment(first)
        with self.assertRaisesRegex(ValueError,'CHAIN_MISMATCH'):
            self.importer.import_segment(segment([wire(seq=2)],name='two'))
        with self.assertRaises(sqlite3.IntegrityError):
            self.importer.import_segment(segment([wire(seq=2)],previous=first['sha256']))
        self.assertEqual(self.count('received_records'),1)

    def test_declared_restart_gap_and_overlap_provenance_are_retained(self):
        first=segment([wire()]);self.importer.import_segment(first)
        rows=[dict(kind='restart',seq=2,session='b',observed_at=timestamp(122.),previous_session='a',lost_records=None),
              dict(kind='gap',seq=3,session='b',observed_at=timestamp(123.),reason='collector_outage',lost_records=None),
              wire(seq=4,at=124.,session='b'),snapshot(seq=5,at=125.,session='b',refs=[dict(seq=4,index=0)])]
        self.importer.import_segment(segment(rows,name='two',previous=first['sha256']))
        result=self.importer.snapshots()[0]
        self.assertEqual([r['kind'] for r in result['gap_provenance']],['restart','gap'])
        self.assertTrue(all(r['lost_records'] is None for r in result['gap_provenance']))
        self.assertFalse(result['receipt_continuity_verified'])
        self.assertEqual(result['links'][0]['revision'],1)

    def test_undeclared_restart_and_fabricated_gap_loss_rejected(self):
        self.rejects_atomic([wire(),wire(seq=2,session='b')],'UNDECLARED_RESTART')
        self.rejects_atomic([wire(),dict(kind='restart',seq=2,session='b',observed_at=timestamp(122.),previous_session='wrong',lost_records=None)],'RESTART_PREDECESSOR')
        self.rejects_atomic([dict(kind='gap',seq=1,session='a',observed_at=timestamp(122.),reason='loss',lost_records=False)],'INVALID_GAP_LOSS')

    def test_snapshot_cannot_omit_or_add_bar_refs_or_mutate_values(self):
        for mutate in ('extra','missing','value','index','future','duplicate'):
            with self.subTest(mutate=mutate):
                s=snapshot()
                if mutate=='extra':s['bar_refs']['unknown']=[]
                elif mutate=='missing':s['bar_refs']['minute_bars']=[]
                elif mutate=='value':s['native_state']['minute_bars'][0]['high']=103.
                elif mutate=='index':s['bar_refs']['minute_bars'][0]['index']=1
                elif mutate=='future':s['bar_refs']['minute_bars'][0]['seq']=2
                else:
                    s['native_state']['minute_bars']*=2;s['bar_refs']['minute_bars']*=2
                self.rejects_atomic([wire(),s], 'COVERAGE_REQUIRED|REVISION_MISMATCH|OCCURRENCE_NOT_FOUND|REFERENCE_REQUIRED')

    def test_snapshot_future_receipt_and_empty_snapshot_rejected(self):
        self.rejects_atomic([wire(at=123.),snapshot(at=122.)],'CLOCK_REGRESSION')
        self.rejects_atomic([snapshot(seq=1,bars=[],refs=[])],'NO_FINAL_BARS')

    def test_atomic_storage_failure_rolls_back_seal_raw_bars_and_snapshot(self):
        self.store.db.executescript("CREATE TRIGGER fail_snapshot BEFORE INSERT ON received_snapshots BEGIN SELECT RAISE(ABORT,'fixture disk failure'); END;")
        self.rejects_atomic([wire(),snapshot()],'fixture disk failure')
        self.store.db.execute('DROP TRIGGER fail_snapshot')
        self.importer.import_segment(segment([wire(),snapshot()]))
        self.assertEqual(self.count('received_snapshots'),1)

    def test_record_byte_caps_stop_without_deleting_existing_evidence(self):
        data=segment([wire()]);self.importer.import_segment(data)
        self.store.max_records=1
        with self.assertRaisesRegex(RuntimeError,'RECORD_CAP'):
            self.importer.import_segment(segment([wire(seq=2)],name='two',previous=data['sha256']))
        self.assertEqual(self.count('received_records'),1)
        path=Path(self.tmp.name)/'huge.json';path.write_bytes(b' '*(MAX_BYTES+1))
        with self.assertRaisesRegex(ValueError,'BYTE_CAP'):self.importer.import_file(path)

    def test_live_candidate_plan_uses_original_bound_occurrence_across_restart(self):
        b=bar();state=dict(minute_bars=[b,bar(start=120)],five_bars=[])
        raw=wire(bars=[dict(bar=b,final=True,final_basis='synthetic_test_only'),dict(bar=bar(start=120),final=True,final_basis='synthetic_test_only')],at=181.)
        s=snapshot(at=182.,bars=state['minute_bars'],refs=[dict(seq=1,index=0),dict(seq=1,index=1)])
        self.importer.import_segment(segment([raw,s]));key=self.importer.snapshots()[0]['snapshot_hash']
        plan=dict(direction=1,strategy='trend_retest',structure_anchor=98.,price=100.,stop=97.,target1=104.)
        def event(kind,at,**kw):
            return dict(version=VERSION,kind=kind,observed_at=at,data=dict(policy='capture',quote=100.,native_state=state,plan=plan,**kw))
        candidate=self.store.append(event('candidate',182.,received_snapshot_hash=key))
        self.store.close();self.store=LiveEvidenceStore(self.path)
        self.store.append(event('frozen_plan',183.,candidate_record_hash=candidate))
        frozen=json.loads(self.store.db.execute('SELECT payload FROM frozen_plans').fetchone()[0])
        anchor=frozen['provenance']['anchor']
        self.assertEqual(anchor['known_at'],181.)
        self.assertEqual(anchor['received_occurrence']['seq'],1)
        self.assertEqual(anchor['bar_start'],60)
        self.assertEqual(self.store.db.execute('SELECT status FROM dispatch_events').fetchone()[0],'DISABLED')
        self.assertIsNone(json.loads(self.store.db.execute('SELECT payload FROM outcome_events').fetchone()[0])['net_pnl_model'])

    def test_live_binding_rejects_different_state_quote_or_evaluation_time(self):
        self.importer.import_segment(segment([wire(),snapshot()]));key=self.importer.snapshots()[0]['snapshot_hash']
        state=snapshot()['native_state']
        for mismatch in ('state','quote','time','missing'):
            data=dict(policy='capture',native_state=copy.deepcopy(state),quote=100.,received_snapshot_hash=key)
            at=122.
            if mismatch=='state':data['native_state']['other']='tampered'
            elif mismatch=='quote':data['quote']=101.
            elif mismatch=='time':at=123.
            else:data['received_snapshot_hash']='f'*64
            with self.assertRaisesRegex(ValueError,'CAUSAL_MISMATCH|SNAPSHOT_NOT_FOUND'):
                self.store.append(dict(version=VERSION,kind='observation',observed_at=at,data=data))
        self.assertEqual(self.count('live_records'),0)

    def test_retained_real_chart_occurrences_receipts_revisions_preserved_but_no_final_binding(self):
        audit=json.loads(Path('research/zerobase/completion_20261028/source_probe/audit.json').read_text())
        rows=[];retained=[]
        for attempt in audit['websocket']:
            for f in attempt['frames']:
                rows.append(dict(kind='wire',seq=len(rows)+1,session='retained',schema=CHART_SCHEMA,
                    received_at=f['received_at'],wire_hex=f['hex'],wire_sha256=f['sha256']))
                retained.append(f)
        self.assertEqual(len(rows),6)
        self.importer.import_segment(segment(rows))
        stored=[json.loads(r[0]) for r in self.store.db.execute('SELECT payload FROM received_records ORDER BY seq')]
        self.assertEqual([r['wire_hex'] for r in stored],[f['hex'] for f in retained])
        self.assertEqual([r['received_at'] for r in stored],[f['received_at'] for f in retained])
        self.assertEqual(self.count('received_bars'),6)
        self.assertEqual(self.store.db.execute('SELECT count(*) FROM received_bars WHERE final IS NOT NULL').fetchone()[0],0)
        self.assertEqual(self.count('received_snapshots'),0)
        self.assertEqual(self.store.db.execute('SELECT max(revision) FROM received_bars').fetchone()[0],4)
        source=json.loads(self.store.db.execute('SELECT payload FROM received_bars WHERE stream=? AND seq=?',('test',2)).fetchone()[0])
        b=source['bar'];s=snapshot(seq=7,at=receipt_time(rows[-1]['received_at'])+1,bars=[b],refs=[dict(seq=2,index=0)],session='retained')
        with self.assertRaisesRegex(ValueError,'FINAL_RECEIPT_UNVERIFIED'):
            self.importer.import_segment(segment([s],name='two',previous=segment(rows)['sha256']))
        self.assertEqual(self.count('received_records'),6)
        self.assertEqual(self.count('frozen_plans'),0)

    def test_archive_preserves_seals_raw_occurrences_snapshots_and_comparison_reference(self):
        data=segment([wire(),snapshot()]);self.importer.import_segment(data)
        destination=Path(self.tmp.name)/'archive.sqlite3'
        audit=self.store.archive(destination,comparison_reference='synthetic_no_promotion')
        self.assertEqual(audit['sealed_received_streams']['segments'],[data['sha256']])
        self.assertEqual(audit['sealed_received_streams']['records'],2)
        self.assertEqual(audit['sealed_received_streams']['bars'],1)
        self.assertFalse(audit['deletion_performed'])
        with sqlite3.connect(destination) as backup:
            self.assertEqual(backup.execute('SELECT * FROM received_records').fetchall(),
                             self.store.db.execute('SELECT * FROM received_records').fetchall())
            self.assertEqual(backup.execute('SELECT * FROM received_snapshots').fetchall(),
                             self.store.db.execute('SELECT * FROM received_snapshots').fetchall())

    def test_retained_converter_records_actual_attempt_boundaries_and_unknown_loss(self):
        from research.import_received_stream import retained_segment,report
        path=Path('research/zerobase/completion_20261028/source_probe/audit.json')
        data=retained_segment(json.loads(path.read_text()),hashlib.sha256(path.read_bytes()).hexdigest())
        result=report(self.importer,self.importer.import_segment(data))
        self.assertEqual(result['wire_occurrences'],6)
        self.assertEqual(result['final_status_unknown_count'],6)
        self.assertEqual(result['verified_final_bar_count'],0)
        self.assertEqual([r['kind'] for r in result['gap_provenance']],['restart','gap','restart','gap'])
        self.assertTrue(all(r['lost_records'] is None for r in result['gap_provenance']))
        self.assertFalse(result['actual_exchange_ingest_sequence_available'])
        self.assertEqual(result['snapshots'],[])

    def test_native_five_minute_receipt_bound_directly_without_aggregation(self):
        five=dict(bar(start=300),period=300,updated=599)
        a=wire(at=601.,bars=[dict(bar=five,final=True,final_basis='synthetic_test_only')])
        s=snapshot(at=602.,bars=[],refs=[])
        s['native_state']['five_bars']=[five];s['bar_refs']['five_bars']=[dict(seq=1,index=0)]
        self.importer.import_segment(segment([a,s]))
        self.assertEqual(self.count('received_bars'),1)
        self.assertEqual(self.importer.snapshots()[0]['links'][0]['bar']['period'],300)
        self.assertFalse(self.importer.snapshots()[0]['qualified'])

    def test_chart_undocumented_final_field_fails_closed(self):
        raw=dict(x=2,z=4,w=121000,d=dict(a='BTCUSDT',b='1m',c=60,k=119,
            d='100',e='100',f='102',g='98',h='1',i='100',final=True))
        data=wire();data['schema']=CHART_SCHEMA
        data['wire_hex']=canonical(raw).encode().hex()
        data['wire_sha256']=hashlib.sha256(bytes.fromhex(data['wire_hex'])).hexdigest()
        self.rejects_atomic([data],'UNKNOWN_CHART_BAR_SCHEMA')


if __name__=='__main__':unittest.main()
