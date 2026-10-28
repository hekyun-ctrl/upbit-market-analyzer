"""Complete E5 event accounting, explanatory strata and shared-plan timing.

Use unchanged E4 reporter for general event/candidate/fixed-plan accounting;
E5-specific strata are descriptive, never independent or unseen samples.
"""
import csv,gzip,hashlib,json,sys
from collections import Counter
from pathlib import Path
from unittest.mock import patch
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from research import compare_early_reversal_experiment as experiment
from research import render_barrier_chase_report as common
from research import compare_structural_targets as p1
OUT=experiment.OUT


def main():
    with patch.object(common,'experiment',experiment),patch.object(common,'OUT',OUT):
        common.main()
    with gzip.open(OUT/'gate_decisions.csv.gz','rt',newline='') as f:decisions=list(csv.DictReader(f))
    cards=json.loads((OUT/'trade_cards.json').read_text())
    summary=json.loads((OUT/'comparison.json').read_text());rows=[];scans=[]
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
                ('context_and_rsi_observed_aligned',lambda g:all(g[k]['status']=='passed' for k in ('context','rsi')))):
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
                false_reclaim_evaluations=sum(d['first_reject'] in ('e5_first_reclaim_invalidated','e5_original_stop_or_extreme_crossed') for d in confirmations),
                direction_evaluations=dict(Counter(d['direction'] for d in e5)),strata=strata))
            scan=[d for d in local if d['phase']=='e5_structure_scan']
            scans.append(dict(scenario=scenario,policy=policy,scans=len(scan),
                preempted=sum(d.get('scan_status')!='evaluated' for d in scan),
                directional_reasons=dict(Counter(reason for d in scan for reason in (json.loads(d.get('e5_scan') or 'null') or {}).get('sides',{}).values()))))
    p1.write_json(OUT/'e5_route_accounting.json',dict(route=rows,structure_scans=scans,
        limitations=['All development observations reused; no independent5m, actual receipt, fill or funding qualification',
            'Failed RSI/trend recorded even when explanatory; hard data, flat-market and chase failures still block',
            'Repeated evaluations and cost/delay variants are not independent market samples',
            'New strategy family remains unmatched to capture structures without identical canonical anchors',
            'A final closed stop is different from a reclaim invalidation before entry; both recorded, not combined']))
    # Include the wrapper and route outputs in the derivation hash contract.
    path=OUT/'report_manifest.json';manifest=json.loads(path.read_text())
    manifest['command']='python research/render_early_reversal_report.py'
    manifest['code_sha256'].update({p:hashlib.sha256((ROOT/p).read_bytes()).hexdigest() for p in
        ('research/render_early_reversal_report.py','research/render_barrier_chase_report.py')})
    manifest['output_sha256']['e5_route_accounting.json']=hashlib.sha256((OUT/'e5_route_accounting.json').read_bytes()).hexdigest()
    p1.write_json(path,manifest)
    print(json.dumps(rows,ensure_ascii=False,indent=2))

if __name__=='__main__':main()
