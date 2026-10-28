"""Derived E3 attribution, using the earliest observed plan for both entry times.

This counterfactual isolates timing from replanned targets; it is not the actual
policy path and does not use a later plan at an earlier timestamp.
"""
import hashlib
import json
import csv
import gzip
import sys
from collections import Counter
from pathlib import Path
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from research import compare_barrier_chase_experiment as experiment
from research.render_confirmation_report import fixed_plan_entry
from research import compare_structural_targets as p1

OUT=experiment.OUT
replay=experiment.replay


def main():
    summary=json.loads((OUT/'comparison.json').read_text())
    cards=json.loads((OUT/'trade_cards.json').read_text())
    outcomes=replay.load_outcome_bars(replay.DATA/'outcome_candle_logs.jsonl')
    rows=[]
    for scenario,case in summary['experiments'].items():
        model=case['costs'];attrs={replay.CONFIG_MAP[k]:v for k,v in model.items()}
        with patch.multiple(replay.monitor,**attrs):
            for old,new in json.loads(experiment.DECLARATION.read_text())['comparison_pairs']:
                local=[c for c in cards if c['scenario']==scenario and c['policy'] in (old,new)]
                for group in experiment.pair_evidence(local,[],old,new):
                    if group['group']!='matched':continue
                    a,b=group['old'],group['new']
                    shared=min((a,b),key=lambda c:(c['provenance']['confirm_at'],c['policy']))
                    assert shared['provenance']['confirm_at']<=min(a['provenance']['confirm_at'],b['provenance']['confirm_at'])
                    earlier=fixed_plan_entry(shared,b,outcomes);later=fixed_plan_entry(shared,a,outcomes)
                    rows.append(dict(scenario=scenario,event_id=group['event_id'],comparison=old+'_vs_'+new,
                        basis='shared earliest observed confirmed plan; retrospective attribution, not actual policy path',
                        shared_plan_id=shared['plan_id'],shared_plan_known_at=shared['provenance']['confirm_at_source_text'],
                        actual_received_at=None,old=later,new=earlier,
                        net_timing_difference=(earlier['net_pnl_model']-later['net_pnl_model'])
                            if earlier['net_pnl_model'] is not None and later['net_pnl_model'] is not None else None,
                        actual_plan_changes={k:dict(old=a['plan'][k],new=b['plan'][k]) for k in ('stop','target1','target2') if a['plan'][k]!=b['plan'][k]}))
    p1.write_json(OUT/'fixed_plan_attribution.json',dict(diagnostic_only=True,rows=rows,
        assumptions='earliest observed S/T fixed for both actual retained entry quotes; Q capped by shared Q and same risk/margin budget; original fee/stop-slip/funding model; no account fills or funding verification'))
    with gzip.open(OUT/'gate_decisions.csv.gz','rt',newline='') as source:
        decisions=list(csv.DictReader(source))
    accounting=[]
    with (OUT/'trades.csv').open(newline='') as source:
        for row in csv.DictReader(source):
            old,new=row['comparison'].split('_vs_')
            facts={}
            for policy in (old,new):
                local=[d for d in decisions if d['scenario']==row['scenario']
                       and d['event_id']==row['event_id'] and d['policy']==policy]
                confirmations=[d for d in local if d['phase']=='confirmation']
                facts[policy]=dict(candidate_evaluations=sum(d['phase']=='candidate' for d in local),
                    confirmation_evaluations=len(confirmations),
                    confirmation_status='evaluated' if confirmations else 'no_confirmation_evaluation_observed',
                    limitation=None if confirmations else 'No comparator confirmation evaluated for this exact anchor in retained observations; do not invent a failed gate.',
                    recorded_rejections=[dict(phase=d['phase'],evaluated_at=d['evaluated_at'],reason=d['first_reject'],detail=d['plan_reject_detail']) for d in local if d['first_reject']],
                    simultaneous_other_event_actions=[dict(event_id=d['event_id'],phase=d['phase'],state=d['state'],evaluated_at=d['evaluated_at'],reason=d['first_reject'],detail=d['plan_reject_detail'])
                        for d in decisions if d['scenario']==row['scenario'] and d['policy']==policy
                        and d['event_id'] and d['event_id']!=row['event_id']
                        and d['evaluated_at'] in {x['evaluated_at'] for x in local if x['phase']=='candidate'}
                        and d['phase'] in ('candidate','alert_eligibility','confirmation')])
            accounting.append(dict(scenario=row['scenario'],comparison=row['comparison'],event_id=row['event_id'],group=row['group'],
                confirmation_lead_seconds=row['confirmation_lead_seconds'] or None,
                outcomes=json.loads(row['outcome_status']),
                actual_policy_net_model=json.loads(row['net_pnl_model']),policies=facts))
    p1.write_json(OUT/'event_accounting.json',accounting)
    with (OUT/'opportunities.csv').open(newline='') as source:
        opportunities=list(csv.DictReader(source))
    detection=[]
    candidate_pairs=[]
    for scenario,case in summary['experiments'].items():
        for policy in case['arms']:
            local=[d for d in decisions if d['scenario']==scenario and d['policy']==policy
                   and d['phase']=='candidate' and json.loads(d.get('e1_detection') or 'null')]
            detection.append(dict(scenario=scenario,policy=policy,
                first_retest_evaluations=len(local),
                first_retest_unique_events=len({d['event_id'] for d in local if d['event_id']}),
                qualified_evaluations=sum(d['state']=='qualified' for d in local),
                first_reject=dict(Counter(d['first_reject'] for d in local if d['first_reject'])),
                rejection_detail=dict(Counter(d['plan_reject_detail'] or d['first_reject'] for d in local if d['first_reject'])),
                detector_sources=dict(Counter(json.loads(d['e1_detection'])['source'] for d in local)),
                final_first_retest_alerts=sum(bool(c['plan'].get('_p2_e1_detection')) for c in cards
                    if c['scenario']==scenario and c['policy']==policy)))
        for old,new in json.loads(experiment.DECLARATION.read_text())['comparison_pairs']:
            name=old+'_vs_'+new
            local=[r for r in opportunities if r['scenario']==scenario and r['comparison']==name]
            common=[r for r in local if r['old_candidate_at'] and r['new_candidate_at']]
            candidate_pairs.append(dict(scenario=scenario,comparison=name,
                common_events=len(common),
                old_only_events=sum(bool(r['old_candidate_at']) and not r['new_candidate_at'] for r in local),
                new_only_events=sum(bool(r['new_candidate_at']) and not r['old_candidate_at'] for r in local),
                candidate_lead_seconds=[float(r['candidate_lead_seconds']) for r in common],
                matching_basis='same exact source anchor; absent counterpart is unmatched, not a fabricated lead'))
    p1.write_json(OUT/'candidate_accounting.json',dict(detection=detection,comparisons=candidate_pairs,
        limitation='Repeated evaluations and nominal-delay variants are not independent market events. E4 retains capture momentum/context/RSI/risk checks; original E1/E3 factors remain separately declared.'))
    flow=[]
    for scenario,case in summary['experiments'].items():
        for policy in case['arms']:
            local=[d for d in decisions if d['scenario']==scenario and d['policy']==policy]
            candidates=[d for d in local if d['phase']=='candidate']
            confirmations=[d for d in local if d['phase']=='confirmation']
            alerts=[c for c in cards if c['scenario']==scenario and c['policy']==policy]
            phases={}
            for phase in ('candidate','confirmation','alert_eligibility','watch_scheduling'):
                failed=[d for d in local if d['phase']==phase and d['first_reject']]
                phases[phase]={reason:dict(evaluations=sum((d['plan_reject_detail'] or d['first_reject'])==reason for d in failed),
                    unique_events=len({d['event_id'] for d in failed if d['event_id'] and (d['plan_reject_detail'] or d['first_reject'])==reason}))
                    for reason in sorted({d['plan_reject_detail'] or d['first_reject'] for d in failed})}
            flow.append(dict(scenario=scenario,policy=policy,detected_unique_events=len({d['event_id'] for d in candidates if d['event_id']}),
                candidate_qualified_unique_events=len({d['event_id'] for d in candidates if d['event_id'] and d['state']=='qualified'}),
                confirmation_qualified_unique_events=len({d['event_id'] for d in confirmations if d['event_id'] and d['state']=='qualified'}),
                alert_eligible_unique_events=len({c['event_id'] for c in alerts if c['event_id']}),
                no_canonical_anchor_candidates=sum(not d['event_id'] for d in candidates),phase_rejections=phases))
    e4_rows=[]
    for scenario,case in summary['experiments'].items():
        for policy in case['arms']:
            if not policy.endswith('_e4'):continue
            local=[d for d in decisions if d['scenario']==scenario and d['policy']==policy
                   and d['phase'] in ('candidate','confirmation')]
            executed=[d for d in local if json.loads(d.get('e4_evidence') or 'null')]
            blocked=[d for d in executed if not json.loads(d['e4_evidence'])['location']['passed']]
            barrier=[d for d in executed if d['plan_reject_detail']=='native_target_barrier_conflict']
            e4_rows.append(dict(scenario=scenario,policy=policy,assessments=len(local),
                e4_evaluations=len(executed),e4_not_evaluated=len(local)-len(executed),
                location_failed_evaluations=len(blocked),
                location_failed_events=len({d['event_id'] for d in blocked if d['event_id']}),
                location_failure_reasons=dict(Counter(json.loads(d['e4_evidence'])['location']['reason'] for d in blocked)),
                native_barrier_rejected_evaluations=len(barrier),
                native_barrier_rejected_events=len({d['event_id'] for d in barrier if d['event_id']}),
                evaluations_with_aligned_exception_removed=sum(any(r['original_may_override_single_pivots']
                    for r in json.loads(d['e4_evidence'])['ranked_plan_evaluations']) for d in executed)))
    # Use the first observed E4 anchor assessment ATR for both entries. Never
    # normalize each policy using its own later (potentially lower) volatility.
    entry_distance=[]
    for row in rows:
        old,new=row['comparison'].split('_vs_')
        matched=[c for c in cards if c['scenario']==row['scenario'] and c['event_id']==row['event_id'] and c['policy'] in (old,new)]
        if len(matched)!=2:continue
        a=next(c for c in matched if c['policy']==old);b=next(c for c in matched if c['policy']==new)
        available=sorted((d for d in decisions if d['scenario']==row['scenario'] and d['event_id']==row['event_id'] and d['policy']==new
            and json.loads(d.get('e4_evidence') or 'null') and float(d['evaluated_at'])<=min(a['provenance']['confirm_at'],b['provenance']['confirm_at'])),key=lambda d:float(d['evaluated_at']))
        atr=json.loads(available[0]['e4_evidence'])['location']['proof']['atr1'] if available else None
        worsening=a['plan']['direction']*(b['plan']['price']-a['plan']['price'])
        entry_distance.append(dict(scenario=row['scenario'],comparison=row['comparison'],event_id=row['event_id'],
            signed_new_entry_worsening=worsening,common_discovery_atr1=atr,
            common_atr_basis='earliest E4 plan assessment known before both entries' if atr else 'unavailable',
            signed_worsening_common_atr1=worsening/atr if atr else None))
    p1.write_json(OUT/'e4_gate_accounting.json',dict(flow=flow,evaluations=e4_rows,paired_entries=entry_distance,
        limitations='Execution counts are repeated gate evaluations, not independent trades. Other gates may preempt E4; unexecuted E4 checks remain null. Current-ATR location check is unchanged PR21, common ATR attribution is separate.'))
    inputs=['comparison.json','trade_cards.json','trades.csv','opportunities.csv','gate_decisions.csv.gz','manifest.json','experiment_manifest.json']
    outputs=['fixed_plan_attribution.json','event_accounting.json','candidate_accounting.json','e4_gate_accounting.json']
    p1.write_json(OUT/'report_manifest.json',dict(command='python research/render_barrier_chase_report.py',
        code_sha256={path:hashlib.sha256((ROOT/path).read_bytes()).hexdigest() for path in
            ('research/render_barrier_chase_report.py','research/render_confirmation_report.py')},
        input_sha256={name:hashlib.sha256((OUT/name).read_bytes()).hexdigest() for name in inputs},
        output_sha256={name:hashlib.sha256((OUT/name).read_bytes()).hexdigest() for name in outputs}))
    return rows


if __name__=='__main__':main()
