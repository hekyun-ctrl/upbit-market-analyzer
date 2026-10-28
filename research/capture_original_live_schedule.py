"""Finite changed-scheduler regression, reused retained inputs, never a sender."""
import copy
from datetime import datetime, timezone
import gzip
import json
from pathlib import Path
import sys
import tempfile
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from lbank_signal_evidence import EvidenceLedger,canonical,digest
from research.continuous_comparison import declaration,policy_spec,replay
from research.original_live_schedule import OriginalLiveComparison,config

OUT=ROOT/'research/zerobase/completion_20261028/original_live_schedule'

def save(name,value):
    (OUT/name).write_text(json.dumps(value,ensure_ascii=False,sort_keys=True,indent=2)+'\n')

def gzsave(name,value):
    with (OUT/name).open('wb') as target:
        with gzip.GzipFile(filename='',mode='wb',fileobj=target,mtime=0) as gz:
            gz.write((canonical(value)+'\n').encode())

def main():
    start=datetime.now(timezone.utc).isoformat()
    declaration_file=json.loads((OUT/'experiment_manifest.json').read_text())
    bundles,bars=replay.load_archives(replay.DATA/'archive_chunks.json')
    points=json.loads((replay.DATA/'recorded_quotes.json').read_text())
    interval=declaration_file['interval']
    selected=[p for p in points if p['window']==interval['window'] and
        interval['start_inclusive']<=p['logged_at']<interval['end_exclusive']]
    assert len(selected)==interval['observations']==20
    with tempfile.TemporaryDirectory() as directory,patch.object(replay.monitor,'telegram',side_effect=AssertionError('OUTBOUND_FORBIDDEN')):
        feed=replay.make_feed(directory,bars)
        inputs=[]
        for seq,p in enumerate(selected):
            at=replay.timestamp(p['logged_at']);state,reason=replay.available_state(feed,at,p['price'],0)
            inputs.append(dict(seq=seq,observed_at=at,quote=p['price'],native_state=state,
                source_observation=p,native_diagnostics=dict(reason=reason),
                unavailable_reason=reason if state is None else None))
        manifest=declaration([policy_spec(0),policy_spec(2)],bundles[0]['model_config'],
            purpose='development',source_commit=declaration_file['baseline'],schedule='original_live_entry_ast_v1',
            schedule_config=config(poll_seconds=declaration_file['poll_seconds']))
        reports=[];steps=[];states=[]
        for attempt,restart in (('uninterrupted',False),('restart_after10',True)):
            path=Path(directory)/(attempt+'.db');book=EvidenceLedger(path);runner=OriginalLiveComparison(book,manifest)
            for p in inputs:
                before=digest(p);runner.step(p);assert digest(p)==before
                if restart and p['seq']==9:
                    book.close();book=EvidenceLedger(path);runner=OriginalLiveComparison(book,manifest)
            runner.followups(replay.load_outcome_bars(replay.DATA/'outcome_candle_logs.jsonl'))
            reports.append(runner.report())
            steps.append([json.loads(r[0]) for r in book.db.execute('SELECT payload FROM comparison_steps ORDER BY seq,policy')])
            states.append(list(book.db.execute('SELECT policy,payload FROM comparison_states ORDER BY policy')))
            assert book.db.execute('PRAGMA integrity_check').fetchone()[0]=='ok'
            assert book.db.execute('PRAGMA foreign_key_check').fetchall()==[]
            book.close()
        assert reports[0]==reports[1] and steps[0]==steps[1] and states[0]==states[1]
        unseen_manifest=declaration(manifest['policies'],manifest['costs'],purpose='unseen',
            source_commit=declaration_file['baseline'],schedule=manifest['schedule'],schedule_config=manifest['schedule_config'])
        book=EvidenceLedger(Path(directory)/'unseen.db');runner=OriginalLiveComparison(book,unseen_manifest)
        for p in inputs:runner.step(p)
        unseen=runner.report();book.close()
        assert all(m['valid_observations']==0 and m['net_pnl_model'] is None for m in unseen['metrics'].values())
        save('comparison_report.json',reports[0]);save('unseen_blocked_report.json',unseen)
        gzsave('steps.json.gz',steps[0]);gzsave('final_policy_states.json.gz',states[0])
        save('input_index.json',[dict(seq=p['seq'],source=p['source_observation'],
            observed_at=p['observed_at'],state_sha256=digest(p['native_state']),point_sha256=digest(p),
            actual_historical_quote_receipt=None,all_original_poll_inputs_available=False) for p in inputs])
        previous=json.loads((OUT.parent/'gate_trace/continuous_report.json').read_text())
        save('schedule_comparison.json',dict(original_entry_ast=reports[0]['metrics'],
            retained_replay_previous=previous['metrics'],
            note='Same finite retained input subset, different declared schedules. Missing original quote polls are not reconstructed. No live-runtime equivalence or independent sample claim.'))
        save('checks.json',dict(started_at=start,ended_at=datetime.now(timezone.utc).isoformat(),
            observations=20,policy_observations=40,independent_new_samples=0,
            uninterrupted_restart_exact=True,inputs_unchanged=True,qualified_unseen=0,
            exact_original_selected_ast=True,original_entry_branch_unmodified=True,
            source_ast_sha256=manifest['schedule_config']['source_ast_sha256'],
            actual_historical_quote_receipts=0,official_final_bars=0,
            full_original_poll_stream_available=False,live_runtime_verified=False,
            sqlite_integrity='ok',foreign_key_errors=[],new_network_requests=0,sends=0,orders=0,
            metrics=reports[0]['metrics'],profitability_validated=False))
    print((OUT/'checks.json').read_text())

if __name__=='__main__':main()
