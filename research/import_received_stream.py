"""Offline sealed-receipt import/report. No network, strategy, sender or order API.

Examples:
 python research/import_received_stream.py --sealed-input segment.json --database /tmp/evidence.sqlite3
 python research/import_received_stream.py --retained-audit research/zerobase/completion_20261028/source_probe/audit.json --sealed-output /tmp/retained.json --database /tmp/retained.sqlite3 --report /tmp/report.json

The retained converter assigns retrospective archive occurrence ordinals. These
are NOT an exchange sequence, original collector ingest sequence, or new samples.
Only original audit receipt/attempt times are used. A seal is not authenticity.
"""
import argparse
import hashlib
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from lbank_live_evidence import LiveEvidenceStore
from lbank_received_stream import (ReceivedStreamImporter, SCHEMA, CHART_SCHEMA,
    MAX_BYTES, BLOCKERS, seal_segment, strict_json)
from lbank_signal_evidence import canonical


def retained_segment(audit, audit_sha256):
    rows=[];previous=None
    # Empty official-schema attempts cannot be relabelled as chart receipts.
    for index, attempt in enumerate(audit['websocket']):
        if not attempt['frames']:
            continue
        if attempt['basis'] != 'existing_collector_same_feed_not_independent':
            raise ValueError('RETAINED_AUDIT_SCHEMA_NOT_REVIEWED')
        session='retained-audit-attempt-'+str(index)
        rows.append(dict(kind='restart',seq=len(rows)+1,session=session,
            observed_at=attempt['requested_at'],previous_session=previous,lost_records=None))
        for frame in attempt['frames']:
            rows.append(dict(kind='wire',seq=len(rows)+1,session=session,schema=CHART_SCHEMA,
                received_at=frame['received_at'],wire_hex=frame['hex'],wire_sha256=frame['sha256']))
        rows.append(dict(kind='gap',seq=len(rows)+1,session=session,
            observed_at=attempt['ended_at'],reason='finite_probe_end; transport_loss_extent_unknown',lost_records=None))
        previous=session
    return seal_segment(dict(schema=SCHEMA,stream='retained-source-probe-'+audit_sha256,
        segment='retained-forming-frames-v1',previous=None,records=rows))


def report(importer, result):
    db=importer.db
    records=[dict(stream=r[0], **json.loads(r[1])) for r in db.execute(
        'SELECT stream,payload FROM received_records ORDER BY stream,seq')]
    bars=[dict(stream=r[0], **json.loads(r[1])) for r in db.execute(
        'SELECT stream,payload FROM received_bars ORDER BY stream,seq,bar_index')]
    wires=[r for r in records if r['kind']=='wire']
    return dict(schema='received_stream_import_report_v1',import_result=result,
        qualified=False,source_blockers=BLOCKERS,new_network_requests=0,
        wire_occurrences=len(wires),unique_wire_hashes=len({r['wire_sha256'] for r in wires}),
        exact_receipts=[{k:r[k] for k in ('stream','seq','session','received_at','wire_sha256')} for r in wires],
        bars=bars,verified_final_bar_count=sum(b['final'] is True for b in bars),
        final_status_unknown_count=sum(b['final'] is None for b in bars),
        gap_provenance=[r for r in records if r['kind'] in ('gap','restart')],
        snapshots=importer.snapshots(),receipt_continuity_verified=False,
        integrity=db.execute('PRAGMA integrity_check').fetchone()[0],
        foreign_key_errors=db.execute('PRAGMA foreign_key_check').fetchall(),
        actual_exchange_ingest_sequence_available=False,raw_to_snapshot_qualification=False,
        profitability_validated=False,independent_5m_verified=False,candle_volume_unit_verified=False)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    choice=parser.add_mutually_exclusive_group(required=True)
    choice.add_argument('--sealed-input',type=Path)
    choice.add_argument('--retained-audit',type=Path)
    parser.add_argument('--sealed-output',type=Path)
    parser.add_argument('--database',required=True,type=Path)
    parser.add_argument('--report',type=Path)
    args=parser.parse_args()
    if args.sealed_output and not args.retained_audit:
        parser.error('--sealed-output requires --retained-audit')
    sealed=None
    if args.retained_audit:
        with args.retained_audit.open('rb') as source:raw=source.read(MAX_BYTES+1)
        if len(raw)>MAX_BYTES:raise ValueError('RETAINED_AUDIT_BYTE_CAP')
        sealed=retained_segment(strict_json(raw.decode()),hashlib.sha256(raw).hexdigest())
    store=LiveEvidenceStore(args.database)
    try:
        importer=ReceivedStreamImporter(store)
        result=importer.import_segment(sealed) if sealed else importer.import_file(args.sealed_input)
        value=report(importer,result)
        if args.sealed_output:
            # Refuse overwrite of a prior sealed input artifact.
            with args.sealed_output.open('x') as target:target.write(canonical(sealed)+'\n')
        rendered=json.dumps(value,ensure_ascii=False,sort_keys=True,indent=2)+'\n'
        if args.report:args.report.write_text(rendered)
        else:print(rendered,end='')
    finally:store.close()


if __name__=='__main__':main()
