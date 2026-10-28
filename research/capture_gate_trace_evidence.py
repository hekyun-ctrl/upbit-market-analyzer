"""Finite predeclared changed-path regressions; no network/full-arm replay."""
import copy
from contextlib import ExitStack
from datetime import datetime,timezone
import gzip
import hashlib
import json
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from lbank_gate_trace import evaluate
from lbank_live_evidence import LiveEvidenceStore
from lbank_signal_evidence import EvidenceLedger,canonical,digest
from research.continuous_comparison import ContinuousComparison,declaration,policy_spec
from research import compare_entry_refinement as replay
from research.compare_signal_decisions import Trace
from research.compare_structural_targets import P1Trace

OUT=ROOT/'research/zerobase/completion_20261028/gate_trace'
ENABLED=SimpleNamespace(enabled=True)


def save(name,value):
    (OUT/name).write_text(json.dumps(value,ensure_ascii=False,sort_keys=True,indent=2)+'\n')


def gzsave(name,value):
    with (OUT/name).open('wb') as raw:
        with gzip.GzipFile(filename='',mode='wb',fileobj=raw,mtime=0) as target:
            target.write((canonical(value)+'\n').encode())


def main():
    started=datetime.now(timezone.utc).isoformat()
    declared=json.loads((OUT/'experiment_manifest.json').read_text())
    bundles,bars=replay.load_archives(replay.DATA/'archive_chunks.json')
    points=json.loads((replay.DATA/'recorded_quotes.json').read_text())
    config=bundles[0]['model_config'];attrs={replay.CONFIG_MAP[k]:v for k,v in config.items()}
    attrs['ENTRY_REFINEMENT_MODE']='capture'
    saved=json.loads((ROOT/'research/zerobase/entry_location_20261025/comparison.json').read_text())
    candidate_times={p['at'] for w in saved['experiments']['capture_delay0']['windows'].values() for p in w['opportunities']}
    with tempfile.TemporaryDirectory() as directory, patch.multiple(replay.monitor,**attrs), patch.object(replay.monitor,'telegram',side_effect=AssertionError('SEND_FORBIDDEN')):
        feed=replay.make_feed(directory,bars)
        chosen=[]
        for point in points:
            at=replay.timestamp(point['logged_at']);state,_=replay.available_state(feed,at,point['price'],0)
            if state and (len(chosen)<6 or replay.iso(at) in candidate_times):chosen.append((point,state,at))
            if len(chosen)==12:break
        cases=[]
        store=LiveEvidenceStore(Path(directory)/'live.sqlite3')
        for point,state,at in chosen:
            original=copy.deepcopy(state);baseline_diag={}
            baseline=replay.monitor.classify(state['prices'],native_state=state,diagnostics=baseline_diag)
            diag={};traced=evaluate(ENABLED,replay.monitor.classify,state['prices'],native_state=state,diagnostics=diag,
                trace_context=dict(native_state=state,quote=point['price'],evaluated_at=at))
            trace=diag.pop('execution_trace')
            assert (baseline,baseline_diag)==(traced,diag)
            assert state==original
            assert trace['complete'],trace['unavailable_reason']
            live_envelope=dict(version=2,kind='candidate',observed_at=at,data=dict(policy='capture',
                quote=point['price'],native_state=state,plan=traced,reason=None if traced else diag['reason'],
                diagnostics=dict(diag,execution_trace=trace)))
            envelope_bytes=len(canonical(live_envelope).encode())
            assert envelope_bytes<=512*1024
            key=store.append(live_envelope)
            cases.append(dict(source_observation=point,input_sha256=digest(original),
                original_output_sha256=digest(dict(plan=baseline,diagnostics=baseline_diag)),
                traced_output_sha256=digest(dict(plan=traced,diagnostics=diag)),
                input_unchanged=state==original,qualified_candidate=traced is not None,reason=diag['reason'],
                live_record_hash=key,live_envelope_bytes=envelope_bytes,execution_trace=trace,actual_historical_quote_receipt=None))
        assert len(cases)==12
        live_decisions=[json.loads(r[0]) for r in store.db.execute('SELECT payload FROM decision_events')]
        assert len(live_decisions)==12 and all(d['actual_gate_vector_available'] for d in live_decisions)
        assert all(not d['full_input_receipt_coverage_verified'] for d in live_decisions)
        store.close()
        gzsave('retained_trace_cases.json.gz',cases)
        interval=declared['continuous_interval']
        selected=[p for p in points if p['window']==interval['window'] and interval['start_inclusive']<=p['logged_at']<interval['end_exclusive']]
        assert len(selected)==interval['observations']==20
        inputs=[]
        for seq,p in enumerate(selected):
            at=replay.timestamp(p['logged_at']);state,reason=replay.available_state(feed,at,p['price'],0)
            inputs.append(dict(seq=seq,observed_at=at,quote=p['price'],native_state=state,
                source_observation=p,unavailable_reason=reason if state is None else None))
        policies=[policy_spec(0),policy_spec(2)]
        manifest=declaration(policies,config,purpose='development',source_commit=declared['source_head'])
        outcomes=replay.load_outcome_bars(replay.DATA/'outcome_candle_logs.jsonl')
        reports=[];all_steps=[];state_dumps=[]
        for attempt,restart in (('uninterrupted',False),('restart_after10',True)):
            path=Path(directory)/(attempt+'.sqlite3');book=EvidenceLedger(path);runner=ContinuousComparison(book,manifest)
            for p in inputs:
                runner.step(p)
                if restart and p['seq']==9:
                    book.close();book=EvidenceLedger(path);runner=ContinuousComparison(book,manifest)
            runner.followups(outcomes)
            report=runner.report();reports.append(report)
            all_steps.append([json.loads(r[0]) for r in book.db.execute('SELECT payload FROM comparison_steps ORDER BY seq,policy')])
            state_dumps.append([r for r in book.db.execute('SELECT policy,payload FROM comparison_states ORDER BY policy')])
            assert book.db.execute('PRAGMA integrity_check').fetchone()[0]=='ok'
            assert book.db.execute('PRAGMA foreign_key_check').fetchall()==[]
            book.close()
        assert reports[0]==reports[1] and all_steps[0]==all_steps[1] and state_dumps[0]==state_dumps[1]
        # Exact existing runner and frozen adapters on this finite interval only.
        old_outputs={}
        for spec,cls in zip(policies,(Trace,P1Trace)):
            book=EvidenceLedger(Path(directory)/(cls.__name__+'-original.sqlite3'))
            trace=cls(book,'capture','finite_original_control',0,config)
            with ExitStack() as hooks:
                trace.patches(hooks)
                available=replay.available_state
                def observed(*a,**kw):
                    state,reason=available(*a,**kw)
                    trace.now=a[1]
                    trace.quote_source=next(copy.deepcopy(p) for p in selected if replay.timestamp(p['logged_at'])==trace.now)
                    return state,reason
                hooks.enter_context(patch.object(replay,'available_state',observed))
                output=replay.replay_window(selected,feed,0)
            old_outputs[cls.__name__]=output
            current=json.loads(dict(state_dumps[0])[cls.__name__])
            actual=[replay.compact_plan(c['plan']) for c in current['plans']]
            old=[{k:v for k,v in p.items() if k not in ('at','watch_at','evidence_plan_id','evidence_event_id')} for p in output['signals']]
            # Evidence adapter adds two explanatory null keys to compact output.
            for p in old:p.pop('entry_location',None);p.pop('location_evidence',None)
            assert actual==old,(cls.__name__,actual,old)
            assert current['watches']==output['watches'] and current['expired']==output['expired']
            assert current['observations']==output['quote_observations']
            book.close()
        unseen_manifest=declaration(policies,config,purpose='unseen',source_commit=declared['source_head'])
        unseen_book=EvidenceLedger(Path(directory)/'unseen.sqlite3');unseen=ContinuousComparison(unseen_book,unseen_manifest)
        for p in inputs:unseen.step(p)
        unseen_report=unseen.report();unseen_book.close()
        assert all(v['valid_observations']==0 and v['net_pnl_model'] is None for v in unseen_report['metrics'].values())
        gzsave('continuous_steps.json.gz',all_steps[0]);save('continuous_report.json',reports[0])
        save('continuous_original_controls.json',old_outputs)
        save('unseen_qualification_report.json',unseen_report)
        save('continuous_input_index.json',[dict(seq=p['seq'],source_observation=p['source_observation'],
            observed_at=p['observed_at'],native_state_sha256=digest(p['native_state']),
            input_sha256=digest(p),actual_historical_receive_time=None) for p in inputs])
        save('retained_checks.json',dict(started_at=started,ended_at=datetime.now(timezone.utc).isoformat(),
            trace_cases=12,qualified_candidates=sum(c['qualified_candidate'] for c in cases),
            all_original_policy_outputs_equal=True,all_input_states_unchanged=True,complete_actual_gate_traces=12,
            continuous_observations=20,policies=2,independent_market_sample_count=0,
            uninterrupted_and_restart_exactly_equal=True,finite_existing_replay_control_exact=True,
            continuous_metrics=reports[0]['metrics'],qualified_unseen_observations=0,
            actual_quote_receipts_reconstructed=0,actual_native_final_receipts=0,
            normalized_quote_response_binding_test='synthetic mechanics only; no new network request',
            all_source_blockers_unchanged=True,storage_integrity='ok',foreign_key_errors=[],
            new_network_requests=0,full96arm_replay_repeated=False,sends=0,orders=0,profitability_validated=False))
    print((OUT/'retained_checks.json').read_text())

if __name__=='__main__':main()
