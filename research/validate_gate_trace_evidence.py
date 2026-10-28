"""Verify hashes, exact policy outputs and explicit unqualified zero/null states."""
import gzip
import hashlib
import json
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/'research/zerobase/completion_20261028/gate_trace'


def main():
    manifest=json.loads((OUT/'manifest.json').read_text())
    for name,expected in manifest['file_sha256'].items():
        assert hashlib.sha256((ROOT/name).read_bytes()).hexdigest()==expected,name
    for name,expected in manifest['decompressed_sha256'].items():
        assert hashlib.sha256(gzip.decompress((OUT/name).read_bytes())).hexdigest()==expected,name
    cases=json.load(gzip.open(OUT/'retained_trace_cases.json.gz','rt'))
    assert len(cases)==12 and sum(c['qualified_candidate'] for c in cases)==6
    for c in cases:
        assert c['original_output_sha256']==c['traced_output_sha256'] and c['input_unchanged']
        t=c['execution_trace'];assert t['complete'] and not t['full_input_receipt_coverage_verified']
        assert t['calls'][0]['name']=='classify' and t['calls'][0]['completed']
        assert c['live_envelope_bytes']<=512*1024
        for name,sha in t['source_sha256'].items():assert hashlib.sha256((ROOT/name).read_bytes()).hexdigest()==sha,name
    report=json.loads((OUT/'continuous_report.json').read_text())
    assert report['independent_policy_state'] and report['all_observations_retained']
    assert not report['qualified'] and not report['profitability_validated']
    for name,sha in report['manifest']['code_sha256'].items():assert hashlib.sha256((ROOT/name).read_bytes()).hexdigest()==sha,name
    metrics=report['metrics'];assert metrics['Trace']['trade_count']==1 and metrics['Trace']['stop_first_count']==1
    assert abs(metrics['Trace']['net_pnl_model']+9.35927984656063)<1e-10
    assert metrics['P1Trace']['trade_count']==0 and metrics['P1Trace']['net_pnl_model']==0
    assert metrics['P1Trace']['unresolved_ratio'] is None
    assert len(report['pairing'])==1 and report['pairing'][0]['group']=='excluded'
    assert report['pairing'][0]['confirmation_lead_seconds'] is None
    assert report['pairing'][0]['gate_reasons']
    steps=json.load(gzip.open(OUT/'continuous_steps.json.gz','rt'))
    assert len(steps)==40 and {s['seq'] for s in steps}==set(range(20))
    assert sum(not s['no_entry'] for s in steps)==1
    assert sum(s['no_signal_interval'] for s in steps)==39
    unseen=json.loads((OUT/'unseen_qualification_report.json').read_text())
    assert all(s['observations']==20 and s['unavailable']==20 and s['valid_observations']==0 and s['net_pnl_model'] is None for s in unseen['metrics'].values())
    checks=json.loads((OUT/'retained_checks.json').read_text())
    assert checks['uninterrupted_and_restart_exactly_equal'] and checks['finite_existing_replay_control_exact']
    assert checks['new_network_requests']==checks['qualified_unseen_observations']==0
    assert checks['all_source_blockers_unchanged'] and checks['storage_integrity']=='ok'
    frozen=json.loads((OUT/'frozen_strategy_verification.json').read_text())
    assert frozen['production_strategy_function_asts_unchanged'] and frozen['frozen_research_and_zb_blobs_unchanged']
    result=dict(schema='gate_trace_orchestration_validation_v1',passed=True,
        files_verified=len(manifest['file_sha256']),decompressed_files_verified=len(manifest['decompressed_sha256']),
        original_actual_policy_outputs_equal=12,complete_execution_traces=12,
        finite_continuous_observations=20,independent_policy_states=2,
        restart_and_original_finite_replay_exact=True,qualified_unseen_observations=0,
        source_blockers_unchanged=True,profitability_validated=False,deployment_performed=False)
    (OUT/'validation.json').write_text(json.dumps(result,indent=2)+'\n')
    print(json.dumps(result,sort_keys=True))

if __name__=='__main__':main()
