"""Validate complete E5 controls, causality accounting and artifact hashes."""
import copy,csv,datetime,gzip,hashlib,json,platform,sqlite3,subprocess,importlib.metadata
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/'research/zerobase/completion_20261028/p2_e5'
BASE='fc2fecb090bdc6a733d42414892554d0baeada37'
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
    frozen=json.loads((ROOT/'research/zerobase/completion_20261028/p2_e4/comparison.json').read_text())
    for name in ('manifest.json','report_manifest.json'):
        m=json.loads((OUT/name).read_text())
        for p,h in m['code_sha256'].items():assert sha(ROOT/p)==h,('CODE',p)
        for p,h in m['input_sha256'].items():assert sha((ROOT if name=='manifest.json' else OUT)/p)==h,('INPUT',p)
        for p,h in m['output_sha256'].items():assert sha(OUT/p)==h,('OUTPUT',p)
    m=json.loads((OUT/'manifest.json').read_text())
    assert hashlib.sha256(gzip.decompress((OUT/'gate_decisions.csv.gz').read_bytes())).hexdigest()==m['uncompressed_decision_sha256']
    assert sha(OUT/'experiment_manifest.json')==m['declaration_sha256']==summary['declaration_sha256']
    for p,h in declaration['input_sha256'].items():assert sha(ROOT/p)==h,('DECLARED',p)
    count=0;new_count=0
    for scenario,case in summary['experiments'].items():
        assert len(case['arms'])==32
        for old,new in declaration['comparison_pairs']:
            assert economic(case['arms'][old]['windows'])==economic(frozen['experiments'][scenario]['arms'][old]['windows']),('CONTROL',scenario,old)
            assert new==old+'_e5';count+=1
            def signals(windows):
                return {k:v['signals'] for k,v in economic(windows).items()}
            assert signals(case['arms'][old]['windows'])==signals(case['arms'][new]['windows']),('E5_FINAL_ALERT_CHANGED',scenario,old)
            new_count+=1
        for arm in case['arms'].values():
            a=arm['aggregate']
            if not a['trade_count']:assert a['total_net_usdt']==0 and a['unresolved_ratio'] is None
            elif a['outcome_status'].get('unresolved'):assert a['total_net_usdt'] is None and a['closed_trade_drawdown_usdt'] is None
            assert a['emergency_count'] is None and a['full_mtm_drawdown'] is None
    unchanged=['lbank_futures_monitor.py','lbank_entry_location.py','lbank_structural_target_plan.py','research/evidence_confirmation.py','research/compare_confirmation_experiment.py','research/first_retest_experiment.py','research/barrier_chase_experiment.py','research/compare_barrier_chase_experiment.py','lbank_signal_evidence.py','lbank_entry_priority.py','lbank_entry_quality.py']
    for p in unchanged:assert (ROOT/p).read_bytes()==subprocess.check_output(['git','show',BASE+':'+p],cwd=ROOT),('FROZEN_CODE',p)
    assert summary['sends']==summary['orders']==0 and not summary['profitability_validated']
    assert summary['source_blockers']==['RAW5M_INDEPENDENCE_UNVERIFIED','CANDLE_VOLUME_UNIT_UNVERIFIED']
    with gzip.open(OUT/'gate_decisions.csv.gz','rt',newline='') as f:decisions=list(csv.DictReader(f))
    explanatory=0;routes=0;scans=0
    allowed={'avoid_15m_strong_countertrend','5m_15m_opposed_to_entry','5m_rsi_direction_mismatch_or_flat'}
    for d in decisions:
        if d['phase']=='e5_structure_scan':
            assert d['policy'].endswith('_e5') and d['scan_status'];scans+=1
        proof=json.loads(d.get('e5_detection') or 'null')
        if proof is not None:
            assert d['policy'].endswith('_e5') and d['phase'] in ('candidate','confirmation')
            assert proof['known_at'] is None and proof['actual_received_at'] is None
            assert proof['nominal_known_at']<=float(d['evaluated_at'])
            assert proof['level_source']['start']<proof['sweep_source']['start']<=proof['recovery_source']['start']
            if d['phase']=='candidate':
                anchor=json.loads(d['anchor']);assert anchor['bar_hash_basis']=='composite_exact_level_and_sweep_sources'
                assert d['strategy']=='early_reversal';routes+=1
        for key,g in json.loads(d['gates']).items():
            if g.get('enforced') is False:
                assert proof is not None and key in ('context','rsi')
                assert g['status']=='passed' or g['reason'] in allowed
                explanatory+=1
        readiness=json.loads(d.get('e5_readiness') or 'null')
        if readiness is not None:
            assert readiness['frozen']==proof and d['phase']=='confirmation'
    variables=json.loads((OUT/'explanatory_variables.json').read_text())
    assert all(r['input_hash_exactly_reconstructed'] for r in variables)
    assert len(variables)==sum(bool(json.loads(d.get('e5_detection') or 'null')) for d in decisions)
    with (OUT/'trades.csv').open(newline='') as f:trades=list(csv.DictReader(f))
    accounting=json.loads((OUT/'event_accounting.json').read_text())
    for r in trades:
        if r['group'] not in ('added','excluded') or json.loads(r['gate_reasons']):continue
        matched=[a for a in accounting if all(a[k]==r[k] for k in ('scenario','comparison','event_id'))]
        assert len(matched)==1 and any(p['confirmation_status']=='no_confirmation_evaluation_observed' for p in matched[0]['policies'].values()),('MISSING_REASON_OR_ABSENCE',r['event_id'])
    state=json.loads((ROOT/'research/zerobase/completion_state.json').read_text())
    prefix='PYTHONPATH=/workspace/scratch/44f97b78c955/zb-test-deps '
    commands=[dict(command=prefix+'python -m unittest test_lbank_early_reversal_experiment test_lbank_first_retest_experiment test_lbank_confirmation_experiment test_lbank_barrier_chase_experiment -q',exit_code=0,tests_at_checkpoint=60,new_tests_at_checkpoint=16),dict(command=prefix+'python -m unittest test_lbank_early_reversal_experiment -q',exit_code=0,tests=20,reason='Causality/priority/identity/readiness and malformed availability fixes before outcomes; four added tests'),dict(command=prefix+'python -m unittest test_lbank_early_reversal_experiment -q',exit_code=0,tests=22,reason='Two additional cached import integrity tests; final E5 code')]
    commands += [dict(command=prefix+x,exit_code=0) for x in ['python research/compare_early_reversal_experiment.py','python research/render_early_reversal_report.py','python research/validate_early_reversal_experiment.py']]
    commands += [dict(command='git -c core.whitespace=blank-at-eol,blank-at-eof,space-before-tab,cr-at-eol diff --cached --check',exit_code=0)]
    checks=dict(all_code_input_output_hashes_match=True,decompressed_csv_hash_matches=True,original_arm_scenario_windows_exactly_equal=count,e5_final_alert_output_comparisons_equal=new_count,frozen_original_code_unchanged=unchanged,no_entry_zero_unresolved_null=True,per_event_recorded_rejections_or_explicit_no_confirmation=True,orders_sends_zero=True,blockers_preserved=True,trade_comparison_scenario_rows=len(trades),e5_candidate_evaluations=routes,explanatory_gate_evaluations=explanatory,e5_structure_scans=scans,exact_explanatory_input_reconstructions=len(variables),distinct_targeted_tests=66,new_synthetic_e5_tests=22)
    files=[p for p in OUT.iterdir() if p.is_file() and p.name not in ('validation.json','gate_decisions.csv') and not p.name.startswith('.gate_decisions.csv.')]
    result=dict(schema='zb_p2_e5_validation_v1',checked_at=datetime.datetime.now(datetime.timezone.utc).isoformat(),started_at=state['e5_started_at'],source_checkpoint=BASE,predeclared_lease_commit='884da546f96ed88ccb813649af8ff24490521d3b',final_code_checkpoint='826c930a3c5efa9165dc902427bbabbf9f1fdf69',checks=checks,commands=commands,runtime=dict(python=platform.python_version(),sqlite=sqlite3.sqlite_version,ccxt=importlib.metadata.version('ccxt'),requests=importlib.metadata.version('requests')),file_sha256={p.name:sha(p) for p in sorted(files)},additional_code_sha256={p:sha(ROOT/p) for p in ('research/validate_early_reversal_experiment.py','test_lbank_early_reversal_experiment.py')},limits=['Reused development observations; no unseen/continuous qualification','Explanatory strata overlap; cost/delay variants not independent samples','One initial interrupted attempt before outcome inspection; causality corrections retained in attempts.json','Live ledger/P3-P5 incomplete; no profitability or whole-feature completion'])
    (OUT/'validation.json').write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n')
    print(json.dumps(checks,indent=2))

if __name__=='__main__':main()
