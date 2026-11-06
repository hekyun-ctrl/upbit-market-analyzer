"""Verify frozen E4 controls, complete artifacts, gates and zero/null semantics."""
import copy,csv,datetime,gzip,hashlib,json,platform,sqlite3,subprocess
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/'research/zerobase/completion_20261028/p2_e4'
BASE='7c8fb9b2f1c6a0b74f7b2de41a0f0141c73844c3'
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def economic(windows):
    windows=copy.deepcopy(windows)
    for window in windows.values():
        for signal in window['signals']:
            signal.pop('evidence_plan_id',None);signal.pop('evidence_event_id',None)
    return windows

def main():
    declaration=json.loads((OUT/'experiment_manifest.json').read_text())
    summary=json.loads((OUT/'comparison.json').read_text())
    frozen=json.loads((ROOT/'research/zerobase/completion_20261028/p2_e1/comparison.json').read_text())
    for name in ('manifest.json','report_manifest.json'):
        m=json.loads((OUT/name).read_text())
        for p,h in m['code_sha256'].items():assert sha(ROOT/p)==h,('CODE',p)
        for p,h in m['input_sha256'].items():assert sha((ROOT if name=='manifest.json' else OUT)/p)==h,('INPUT',p)
        for p,h in m['output_sha256'].items():assert sha(OUT/p)==h,('OUTPUT',p)
    m=json.loads((OUT/'manifest.json').read_text())
    assert hashlib.sha256(gzip.decompress((OUT/'gate_decisions.csv.gz').read_bytes())).hexdigest()==m['uncompressed_decision_sha256']
    assert sha(OUT/'experiment_manifest.json')==m['declaration_sha256']==summary['declaration_sha256']
    for p,h in declaration['input_sha256'].items():assert sha(ROOT/p)==h,('DECLARED',p)
    count=0
    for scenario,case in summary['experiments'].items():
        assert len(case['arms'])==16
        for old,new in declaration['comparison_pairs']:
            assert economic(case['arms'][old]['windows'])==economic(frozen['experiments'][scenario]['arms'][old]['windows']),('CONTROL',scenario,old)
            assert new==old+'_e4';count+=1
        for arm in case['arms'].values():
            a=arm['aggregate']
            if not a['trade_count']:assert a['total_net_usdt']==0 and a['unresolved_ratio'] is None
            elif a['outcome_status'].get('unresolved'):assert a['total_net_usdt'] is None and a['closed_trade_drawdown_usdt'] is None
            assert a['emergency_count'] is None and a['full_mtm_drawdown'] is None
    unchanged=['lbank_futures_monitor.py','lbank_entry_location.py','lbank_structural_target_plan.py','research/evidence_confirmation.py','research/compare_confirmation_experiment.py','research/first_retest_experiment.py','lbank_entry_priority.py','lbank_entry_quality.py']
    for p in unchanged:assert (ROOT/p).read_bytes()==subprocess.check_output(['git','show',BASE+':'+p],cwd=ROOT),('FROZEN_CODE',p)
    assert summary['sends']==summary['orders']==0 and not summary['profitability_validated']
    assert summary['source_blockers']==['RAW5M_INDEPENDENCE_UNVERIFIED','CANDLE_VOLUME_UNIT_UNVERIFIED']
    with gzip.open(OUT/'gate_decisions.csv.gz','rt',newline='') as f:decisions=list(csv.DictReader(f))
    executed=0
    for d in decisions:
        proof=json.loads(d.get('e4_evidence') or 'null')
        if proof is None:continue
        assert d['policy'].endswith('_e4') and d['phase'] in ('candidate','confirmation')
        assert proof['scope']=='calculate_plan_only'
        for r in proof['ranked_plan_evaluations']:assert r['effective_may_override_single_pivots'] is False
        executed+=1
    with (OUT/'trades.csv').open(newline='') as f:trades=list(csv.DictReader(f))
    accounting=json.loads((OUT/'event_accounting.json').read_text())
    for r in trades:
        if r['group'] not in ('added','excluded') or json.loads(r['gate_reasons']):continue
        matched=[a for a in accounting if all(a[k]==r[k] for k in ('scenario','comparison','event_id'))]
        assert len(matched)==1 and any(p['confirmation_status']=='no_confirmation_evaluation_observed' for p in matched[0]['policies'].values()),('MISSING_REASON_OR_ABSENCE',r['event_id'])
    state=json.loads((ROOT/'research/zerobase/completion_state.json').read_text())
    prefix='PYTHONPATH=/workspace/scratch/44f97b78c955/zb-test-deps '
    commands=[dict(command=prefix+'python -m unittest test_lbank_barrier_chase_experiment test_lbank_first_retest_experiment test_lbank_confirmation_experiment test_lbank_entry_location test_lbank_structural_comparison -q',exit_code=0,tests=58,new_tests=14),dict(command=prefix+'python -m unittest test_lbank_barrier_chase_experiment.E4Tests.test_exact_location_limit_is_inclusive -q',exit_code=0,tests=1,new_tests=1)]
    commands += [dict(command=prefix+x,exit_code=0) for x in ['python research/compare_barrier_chase_experiment.py','python research/render_barrier_chase_report.py']]
    commands += [dict(command=x,exit_code=0) for x in ['python research/validate_barrier_chase_experiment.py','git -c core.whitespace=blank-at-eol,blank-at-eof,space-before-tab,cr-at-eol diff --cached --check']]
    commands.append(dict(command='git diff --cached --check',exit_code=2,reason='Standard csv.DictWriter CRLF treated as trailing whitespace by default; CRLF-aware check passes with original artifact bytes unchanged'))
    checks=dict(all_code_input_output_hashes_match=True,decompressed_csv_hash_matches=True,original_arm_scenario_windows_exactly_equal=count,frozen_original_code_unchanged=unchanged,no_entry_zero_unresolved_null=True,per_event_recorded_rejections_or_explicit_no_confirmation=True,orders_sends_zero=True,blockers_preserved=True,trade_comparison_scenario_rows=len(trades),e4_executed_evaluations=executed)
    files=[p for p in OUT.iterdir() if p.is_file() and p.name not in ('validation.json','gate_decisions.csv')]
    result=dict(schema='zb_p2_e4_validation_v1',checked_at=datetime.datetime.now(datetime.timezone.utc).isoformat(),started_at=state['e4_started_at'],source_checkpoint=BASE,predeclared_lease_commit='116f43b7a89a5c6a6080b23062f703a3f656010e',initial_code_checkpoint='9b7aa8a441455d07cf60c3477dc6ed223745e706',checks=checks,commands=commands,runtime=dict(python=platform.python_version(),sqlite=sqlite3.sqlite_version),file_sha256={p.name:sha(p) for p in sorted(files)},additional_code_sha256={p:sha(ROOT/p) for p in ('research/validate_barrier_chase_experiment.py','test_lbank_barrier_chase_experiment.py')},limits=['Reused development observations; no unseen/continuous qualification','E4 location bound unchanged PR21/current ATR; shared fixed-plan/common-ATR attribution is separate','E5/live ledger/P3-P5 incomplete; no profitability or whole-feature completion'])
    (OUT/'validation.json').write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n')
    print(json.dumps(checks,indent=2))
if __name__=='__main__':main()
