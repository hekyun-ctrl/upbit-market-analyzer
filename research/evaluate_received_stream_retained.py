"""Reproduce six real-forming-frame binding failures without inventing receipts."""
from datetime import datetime, timezone
import json
from pathlib import Path
import sys
import tempfile
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from lbank_live_evidence import LiveEvidenceStore
from lbank_received_stream import ReceivedStreamImporter,seal_segment,SCHEMA

OUT=Path(__file__).resolve().parent/'zerobase/completion_20261028/received_stream'


def main():
    sealed=json.loads((OUT/'retained_sealed.json').read_text())
    at=datetime.now(timezone.utc).isoformat();attempts=[]
    with tempfile.TemporaryDirectory() as directory:
        store=LiveEvidenceStore(Path(directory)/'received.sqlite3')
        try:
            importer=ReceivedStreamImporter(store);importer.import_segment(sealed)
            stream=sealed['payload']['stream']
            tail=store.db.execute('SELECT seq,session FROM received_records WHERE stream=? ORDER BY seq DESC LIMIT 1',(stream,)).fetchone()
            for seq,index,payload in store.db.execute('SELECT seq,bar_index,payload FROM received_bars ORDER BY seq').fetchall():
                bar=json.loads(payload)['bar'];minute=bar['period']==60
                row=dict(kind='snapshot',seq=tail[0]+1,session=tail[1],evaluated_at=at,
                    native_state=dict(minute_bars=[bar] if minute else [],five_bars=[] if minute else [bar]),
                    quote=bar['close'],bar_refs=dict(minute_bars=[dict(seq=seq,index=index)] if minute else [],
                                                   five_bars=[] if minute else [dict(seq=seq,index=index)]))
                candidate=seal_segment(dict(schema=SCHEMA,stream=stream,segment='offline-final-binding-attempt-'+str(seq),
                    previous=sealed['sha256'],records=[row]))
                try:importer.import_segment(candidate)
                except ValueError as exc:reason=str(exc)
                else:raise AssertionError('forming frame incorrectly bound')
                assert reason=='FINAL_RECEIPT_UNVERIFIED',reason
                attempts.append(dict(occurrence_seq=seq,bar_index=index,period=bar['period'],result='rejected',reason=reason))
            value=dict(schema='retained_final_binding_attempts_v1',evaluated_at=at,
                evaluation_basis='current_offline_evaluation; not a historical receive timestamp',attempts=attempts,
                remaining_records=store.db.execute('SELECT count(*) FROM received_records').fetchone()[0],
                remaining_snapshots=store.db.execute('SELECT count(*) FROM received_snapshots').fetchone()[0],
                raw_receipts_fabricated=False,profitability_validated=False)
            assert len(attempts)==6 and value['remaining_records']==10 and value['remaining_snapshots']==0
            (OUT/'retained_binding_attempts.json').write_text(json.dumps(value,sort_keys=True,indent=2)+'\n')
            print(json.dumps(value,sort_keys=True))
        finally:store.close()


if __name__=='__main__':main()
