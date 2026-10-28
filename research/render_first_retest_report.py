"""Derived E3 attribution, using the earliest observed plan for both entry times.

This counterfactual isolates timing from replanned targets; it is not the actual
policy path and does not use a later plan at an earlier timestamp.
"""
import hashlib
import json
import csv
import gzip
import sys
from pathlib import Path
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from research import compare_first_retest_experiment as experiment
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
                    recorded_rejections=[dict(phase=d['phase'],evaluated_at=d['evaluated_at'],reason=d['first_reject'],detail=d['plan_reject_detail']) for d in local if d['first_reject']])
            accounting.append(dict(scenario=row['scenario'],comparison=row['comparison'],event_id=row['event_id'],group=row['group'],
                confirmation_lead_seconds=row['confirmation_lead_seconds'] or None,
                outcomes=json.loads(row['outcome_status']),
                actual_policy_net_model=json.loads(row['net_pnl_model']),policies=facts))
    p1.write_json(OUT/'event_accounting.json',accounting)
    inputs=['comparison.json','trade_cards.json','trades.csv','gate_decisions.csv.gz','manifest.json','experiment_manifest.json']
    outputs=['fixed_plan_attribution.json','event_accounting.json']
    p1.write_json(OUT/'report_manifest.json',dict(command='python research/render_first_retest_report.py',
        code_sha256={str(Path(__file__).relative_to(ROOT)):hashlib.sha256(Path(__file__).read_bytes()).hexdigest()},
        input_sha256={name:hashlib.sha256((OUT/name).read_bytes()).hexdigest() for name in inputs},
        output_sha256={name:hashlib.sha256((OUT/name).read_bytes()).hexdigest() for name in outputs}))
    return rows


if __name__=='__main__':main()
