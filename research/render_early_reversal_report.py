"""Complete E5 event accounting, explanatory strata and shared-plan timing.

Use unchanged E4 reporter for general event/candidate/fixed-plan accounting;
E5-specific strata are descriptive, never independent or unseen samples.
"""
import csv,gzip,hashlib,json,sys,tempfile
from collections import Counter
from pathlib import Path
from unittest.mock import patch
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from research import compare_early_reversal_experiment as experiment
from research import render_barrier_chase_report as common
from research import compare_structural_targets as p1
from lbank_signal_evidence import digest
from lbank_entry_refinement import minute_atr
OUT=experiment.OUT

def explanatory_inputs(decisions):
    replay=experiment.replay
    bundles,bars=replay.load_archives(replay.DATA/'archive_chunks.json')
    points=json.loads((replay.DATA/'recorded_quotes.json').read_text())
    by_time={}
    for raw in points:by_time.setdefault(replay.timestamp(raw['logged_at']),[]).append(raw)
    rows=[];cache={}
    with tempfile.TemporaryDirectory(prefix='e5-explanatory-') as directory:
        feed=replay.make_feed(directory,bars)
        for row in decisions:
            proof=json.loads(row.get('e5_detection') or 'null')
            if proof is None:continue
            key=row['scenario'],row['evaluated_at'],row['input_hash']
            if key not in cache:
                delay=15 if row['scenario']=='base_delay15' else 0
                candidates=[]
                for raw in by_time[float(row['evaluated_at'])]:
                    state,reason=replay.available_state(feed,float(row['evaluated_at']),raw['price'],delay)
                    if state is None:continue
                    hashes={digest(b) for name in ('minute_bars','five_bars') for b in state[name]}
                    hashes.add(digest(raw))
                    if digest(sorted(hashes))==row['input_hash']:candidates.append((state,raw))
                assert candidates,('EXPLANATORY_INPUT_NOT_RECONSTRUCTED',key)
                state,raw=candidates[0]
                cache[key]=state,raw
            state,raw=cache[key];d=proof['direction']
            context=replay.monitor.native_context_gate(state,d,'early_reversal')
            rsi=replay.monitor.five_minute_rsi_entry_gate(None,d,setup={'strategy':'early_reversal'},native_state=state)
            original=json.loads(row['gates'])
            for name,result in (('context',context),('rsi',rsi)):
                if original[name]['status']!='not_evaluated':
                    assert original[name]['reason']==result[1] and original[name]['status']==('passed' if result[0] else 'failed')
            rows.append(dict(scenario=row['scenario'],policy=row['policy'],event_id=row['event_id'],phase=row['phase'],
                evaluated_at=float(row['evaluated_at']),input_hash=row['input_hash'],input_hash_exactly_reconstructed=True,
                direction=d,quote=state['quote'],window=raw['window'],known_at=None,actual_received_at=None,
                context_original=dict(passed=context[0],reason=context[1],observed=context[2]),
                rsi_original=dict(passed=rsi[0],reason=rsi[1],observed=rsi[2]),
                original_gate_executed={name:original[name]['status']!='not_evaluated' for name in ('context','rsi')},
                return5_pct=(state['quote']/state['prices'][-6]-1)*100,return15_pct=(state['quote']/state['prices'][-16]-1)*100,
                first_reject=row['first_reject'],plan_reject_detail=row['plan_reject_detail'],
                basis='posthoc explanatory reconstruction of exact same frozen inputs; unexecuted policy gates remain not_evaluated'))
    return rows


def common_entry_distances(decisions,cards,summary):
    replay=experiment.replay
    _,bars=replay.load_archives(replay.DATA/'archive_chunks.json')
    points=json.loads((replay.DATA/'recorded_quotes.json').read_text());rows=[]
    with tempfile.TemporaryDirectory(prefix='e5-common-atr-') as directory:
        feed=replay.make_feed(directory,bars)
        for scenario,case in summary['experiments'].items():
            delay=case['nominal_delay_seconds']
            for old,new in json.loads(experiment.DECLARATION.read_text())['comparison_pairs']:
                local=[c for c in cards if c['scenario']==scenario and c['policy'] in (old,new)]
                for group in experiment.pair_evidence(local,[],old,new):
                    if group['group']!='matched':continue
                    a,b=group['old'],group['new'];at=min(a['provenance']['confirm_at'],b['provenance']['confirm_at'])
                    events=sorted((d for d in decisions if d['scenario']==scenario and d['event_id']==group['event_id']
                        and d['policy'] in (old,new) and d['phase']=='candidate' and float(d['evaluated_at'])<=at),key=lambda d:float(d['evaluated_at']))
                    atr=None;source=None
                    for row in events:
                        for raw in points:
                            if replay.timestamp(raw['logged_at'])!=float(row['evaluated_at']):continue
                            state,_=replay.available_state(feed,float(row['evaluated_at']),raw['price'],delay)
                            if state is None:continue
                            hashes={digest(bar) for name in ('minute_bars','five_bars') for bar in state[name]};hashes.add(digest(raw))
                            if digest(sorted(hashes))!=row['input_hash']:continue
                            atr=minute_atr(state);source=row;break
                        if source:break
                    worsening=a['plan']['direction']*(b['plan']['price']-a['plan']['price'])
                    rows.append(dict(scenario=scenario,comparison=old+'_vs_'+new,event_id=group['event_id'],
                        signed_new_entry_worsening=worsening,common_discovery_atr1=atr,
                        common_atr_input_hash=source['input_hash'] if source else None,
                        common_atr_evaluated_at=float(source['evaluated_at']) if source else None,
                        signed_worsening_common_atr1=worsening/atr if atr else None,
                        basis='earliest exact-input candidate snapshot available before both entries; one common ATR for both'))
    return rows


def main():
    with patch.object(common,'experiment',experiment),patch.object(common,'OUT',OUT):
        common.main()
    with gzip.open(OUT/'gate_decisions.csv.gz','rt',newline='') as f:decisions=list(csv.DictReader(f))
    cards=json.loads((OUT/'trade_cards.json').read_text())
    summary=json.loads((OUT/'comparison.json').read_text());rows=[];scans=[]
    variables=explanatory_inputs(decisions)
    p1.write_json(OUT/'explanatory_variables.json',variables)
    selection=[]
    for row in variables:
        if row['phase']!='candidate':continue
        scenario,policy=row['scenario'],row['policy'];at=row['evaluated_at']
        # Compare numerical timestamps as CSV round-trips retain full precision.
        peers=[d for d in decisions if d['scenario']==scenario and d['policy']==policy and d['phase']=='candidate'
            and float(d['evaluated_at'])==at and d['input_hash']==row['input_hash']]
        qualified=[d for d in peers if d['state']=='qualified']
        opportunities=summary['experiments'][scenario]['arms'][policy]['windows'][row['window']]['opportunities']
        chosen=[p for p in opportunities if experiment.replay.timestamp(p['at'])==at]
        chosen_route=any(p['strategy']=='early_reversal' and p['direction']==row['direction'] for p in chosen)
        conflict=len({d['direction'] for d in qualified})>1
        status=('candidate_rejected' if row['first_reject'] else 'classifier_selected' if chosen_route else
            'opposite_qualified_directions_abstain' if conflict else 'other_same_direction_qualified_strategy_selected')
        if not row['first_reject'] and not chosen_route and not conflict:assert chosen,('SELECTION_NOT_RECONSTRUCTED',row)
        scheduling=[dict(phase=d['phase'],first_reject=d['first_reject'],state=d['state']) for d in decisions
            if d['scenario']==scenario and d['policy']==policy and d['event_id']==row['event_id']
            and float(d['evaluated_at'])==at and d['phase'] in ('watch_scheduling','alert_eligibility')]
        selection.append(dict(scenario=scenario,policy=policy,event_id=row['event_id'],evaluated_at=at,
            input_hash=row['input_hash'],selection_status=status,actual_selected_proposals=chosen,
            qualified_peer_facts=[{k:d[k] for k in ('event_id','strategy','direction','state','first_reject')} for d in qualified],
            observed_scheduling=scheduling,basis='exact retained selected opportunity output and classifier qualified-direction rule; no invented confirmation failure'))
    p1.write_json(OUT/'candidate_selection_accounting.json',selection)
    for scenario,case in summary['experiments'].items():
        for policy in case['arms']:
            if not policy.endswith('_e5'):continue
            local=[d for d in decisions if d['scenario']==scenario and d['policy']==policy]
            e5=[d for d in local if d['phase']=='candidate' and json.loads(d.get('e5_detection') or 'null')]
            alerts=[c for c in cards if c['scenario']==scenario and c['policy']==policy and c['plan'].get('_p2_e5_detection')]
            strata=[]
            for label,predicate in (
                ('countertrend15m',lambda g:g['context'].get('reason')=='avoid_15m_strong_countertrend'),
                ('rsi_opposed_or_flat',lambda g:g['rsi'].get('reason')=='5m_rsi_direction_mismatch_or_flat'),
                ('context_and_rsi_original_gates_passed',lambda g:all(g[k]['status']=='passed' for k in ('context','rsi')))):
                selected=[d for d in e5 if predicate(json.loads(d['gates']))]
                events={d['event_id'] for d in selected if d['event_id']}
                trades=[c for c in alerts if c['event_id'] in events]
                strata.append(dict(label=label,candidate_evaluations=len(selected),unique_events=len(events),
                    final_alerts=len(trades),stop_first=sum(c['first_contact'].get('tp1_before_sl') is False for c in trades),
                    unresolved=sum(c['outcome']['status']!='exact_in_model' for c in trades),
                    limitation='overlapping explanatory groups; no summing as independent events'))
            confirmations=[d for d in local if d['phase']=='confirmation' and json.loads(d.get('e5_detection') or 'null')]
            rows.append(dict(scenario=scenario,policy=policy,e5_candidate_evaluations=len(e5),
                e5_unique_events=len({d['event_id'] for d in e5 if d['event_id']}),
                e5_qualified_candidate_evaluations=sum(d['state']=='qualified' for d in e5),
                first_reject=dict(Counter(d['plan_reject_detail'] or d['first_reject'] for d in e5 if d['first_reject'])),
                final_e5_alerts=len(alerts),final_e5_stop_first=sum(c['first_contact'].get('tp1_before_sl') is False for c in alerts),
                final_e5_unresolved=sum(c['outcome']['status']!='exact_in_model' for c in alerts),
                e4_plan_evaluations=sum(json.loads(d.get('e4_evidence') or 'null') is not None for d in local),
                false_reclaim_evaluations=sum(d['first_reject'] in ('e5_first_reclaim_invalidated','e5_original_stop_or_extreme_crossed') for d in confirmations),
                direction_evaluations=dict(Counter(d['direction'] for d in e5)),strata=strata))
            scan=[d for d in local if d['phase']=='e5_structure_scan']
            scans.append(dict(scenario=scenario,policy=policy,scans=len(scan),
                preempted=sum(d.get('scan_status')!='evaluated' for d in scan),
                directional_reasons=dict(Counter(reason for d in scan for reason in (json.loads(d.get('e5_scan') or 'null') or {}).get('sides',{}).values()))))
    p1.write_json(OUT/'e5_route_accounting.json',dict(route=rows,structure_scans=scans,
        paired_entries=common_entry_distances(decisions,cards,summary),
        limitations=['All development observations reused; no independent5m, actual receipt, fill or funding qualification',
            'Failed RSI/trend recorded even when explanatory; hard data, flat-market and chase failures still block',
            'Repeated evaluations and cost/delay variants are not independent market samples',
            'New strategy family remains unmatched to capture structures without identical canonical anchors',
            'A final closed stop is different from a reclaim invalidation before entry; both recorded, not combined']))
    # Include the wrapper and route outputs in the derivation hash contract.
    path=OUT/'report_manifest.json';manifest=json.loads(path.read_text())
    manifest['command']='python research/render_early_reversal_report.py'
    manifest['code_sha256'].update({p:hashlib.sha256((ROOT/p).read_bytes()).hexdigest() for p in
        ('research/render_early_reversal_report.py','research/render_barrier_chase_report.py','research/compare_entry_refinement.py','lbank_native_entry.py','lbank_sampled_context.py')})
    manifest['output_sha256']['candidate_selection_accounting.json']=hashlib.sha256((OUT/'candidate_selection_accounting.json').read_bytes()).hexdigest()
    manifest['output_sha256']['explanatory_variables.json']=hashlib.sha256((OUT/'explanatory_variables.json').read_bytes()).hexdigest()
    manifest['output_sha256']['e5_route_accounting.json']=hashlib.sha256((OUT/'e5_route_accounting.json').read_bytes()).hexdigest()
    p1.write_json(path,manifest)
    print(json.dumps(rows,ensure_ascii=False,indent=2))

if __name__=='__main__':main()
