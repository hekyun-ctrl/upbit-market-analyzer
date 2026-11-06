"""Validate sealed importer artifacts against original retained transport bytes."""
import hashlib
import json
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from lbank_received_stream import seal_segment

ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/'research/zerobase/completion_20261028/received_stream'


def main():
    manifest=json.loads((OUT/'manifest.json').read_text())
    for name,expected in manifest['file_sha256'].items():
        assert hashlib.sha256((ROOT/name).read_bytes()).hexdigest()==expected,name
    audit=json.loads((ROOT/'research/zerobase/completion_20261028/source_probe/audit.json').read_text())
    sealed=json.loads((OUT/'retained_sealed.json').read_text())
    assert sealed==seal_segment(sealed['payload'])
    frames=[f for a in audit['websocket'] for f in a['frames']]
    wires=[r for r in sealed['payload']['records'] if r['kind']=='wire']
    assert len(frames)==len(wires)==6
    for original,row in zip(frames,wires):
        assert original['received_at']==row['received_at']
        assert original['hex']==row['wire_hex']
        assert hashlib.sha256(bytes.fromhex(row['wire_hex'])).hexdigest()==original['sha256']==row['wire_sha256']
    report=json.loads((OUT/'retained_report.json').read_text())
    duplicate=json.loads((OUT/'retained_duplicate_report.json').read_text())
    assert not report['import_result']['duplicate'] and duplicate['import_result']['duplicate']
    duplicate['import_result']['duplicate']=False
    assert duplicate==report
    assert report['wire_occurrences']==report['unique_wire_hashes']==report['final_status_unknown_count']==6
    assert report['verified_final_bar_count']==0 and report['snapshots']==[]
    assert report['qualified'] is False and report['profitability_validated'] is False
    assert report['independent_5m_verified'] is False and report['candle_volume_unit_verified'] is False
    assert report['raw_to_snapshot_qualification'] is False and report['receipt_continuity_verified'] is False
    assert report['actual_exchange_ingest_sequence_available'] is False
    assert report['integrity']=='ok' and report['foreign_key_errors']==[]
    assert [b['revision'] for b in report['bars']]==[1,1,2,2,3,4]
    assert [b['bar']['period'] for b in report['bars']]==[300,60,60,300,60,60]
    assert [r['received_at'] for r in report['exact_receipts']]==[f['received_at'] for f in frames]
    attempts=json.loads((OUT/'retained_binding_attempts.json').read_text())
    assert len(attempts['attempts'])==6 and all(r['reason']=='FINAL_RECEIPT_UNVERIFIED' for r in attempts['attempts'])
    assert attempts['remaining_records']==10 and attempts['remaining_snapshots']==0
    boundaries=[a for a in audit['websocket'] if a['frames']]
    assert [r['observed_at'] for r in report['gap_provenance']]==[v for a in boundaries for v in (a['requested_at'],a['ended_at'])]
    assert all(r['lost_records'] is None for r in report['gap_provenance'])
    assert report['source_blockers']==['RAW5M_INDEPENDENCE_UNVERIFIED','CANDLE_VOLUME_UNIT_UNVERIFIED']
    result=dict(schema='received_stream_validation_v1',passed=True,file_hashes_verified=len(manifest['file_sha256']),
        exact_raw_bytes_and_original_receipts_preserved=6,transport_attempts=2,explicit_gap_restart_records=4,
        verified_final_bars=0,raw_to_snapshot_bindings=0,real_binding_rejections=6,
        duplicate_import_identical=True,storage_integrity='ok',source_blockers_unchanged=True,
        new_network_requests=0,new_independent_samples=0,profitability_validated=False)
    (OUT/'validation.json').write_text(json.dumps(result,sort_keys=True,indent=2)+'\n')
    print(json.dumps(result,sort_keys=True))


if __name__=='__main__':main()
