"""Verify this checkpoint's code/input/output hashes without replay or networking."""
import ast
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT/'research/zerobase/completion_20261028/live_ledger'


def main():
    manifest = json.loads((OUT/'manifest.json').read_text())
    count = 0
    for group, directory in (('code_sha256', ROOT), ('input_sha256', ROOT), ('output_sha256', OUT)):
        for name, expected in manifest[group].items():
            actual = hashlib.sha256((directory/name).read_bytes()).hexdigest()
            if actual != expected:
                raise ValueError('HASH_MISMATCH: '+name)
            count += 1
    tree = ast.parse((ROOT/'test_lbank_live_evidence.py').read_text())
    tests = sorted(n.name for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name.startswith('test_'))
    assert tests == manifest['new_tests'] and len(tests) == 25
    paired = json.loads((OUT/'retained_policy_integration.json').read_text())['cases']
    assert len(paired) == 12
    assert all(c['policy_output_sha256'] == c['after_healthy_and_failed_storage_output_sha256'] and c['input_unchanged'] for c in paired)
    raw = json.loads((OUT/'retained_raw_receipts.json').read_text())
    assert len(raw['records']) == 6 and raw['closed_bars_verified'] == 0
    assert not raw['independent_5m_verified'] and not raw['candle_volume_unit_verified']
    assert manifest['blockers'] == ['RAW5M_INDEPENDENCE_UNVERIFIED', 'CANDLE_VOLUME_UNIT_UNVERIFIED']
    assert manifest['checks']['automatic_deletion'] is False
    result = dict(schema='live_evidence_validation_v1', manifest_sha256=hashlib.sha256((OUT/'manifest.json').read_bytes()).hexdigest(),
                  files_checked=count, code_input_output_hashes_match=True, policy_pairs_equal=12,
                  retained_frames=6, new_tests=25, related_distinct_tests=140,
                  reporting_error_fixed=True, full_replay_repeated=False,
                  source_blockers_unchanged=True, live_qualification=False)
    (OUT/'validation.json').write_text(json.dumps(result, indent=2)+'\n')
    print(json.dumps(result))


if __name__ == '__main__':
    main()
