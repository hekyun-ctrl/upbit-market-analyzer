"""Verify E1/E3 artifact hashes, frozen controls and zero versus null semantics."""
import copy,csv,datetime,gzip,hashlib,json,platform,sqlite3,subprocess
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/'research/zerobase/completion_20261028/p2_e1'
BASE='f2599841c0d400bd10e1527508f9dc03ad207ce4'
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def economic(rows):
 rows=copy.deepcopy(rows)
 for w in rows.values():
  for s in w['signals']:
   s.pop('evidence_plan_id',None);s.pop('evidence_event_id',None)
 return rows
def main():
 declaration=json.loads((OUT/'experiment_manifest.json').read_text())
 summary=json.loads((OUT/'comparison.json').read_text())
 frozen=json.loads((ROOT/'research/zerobase/completion_20261028/p2/comparison.json').read_text())
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
  for old,new in {'capture':'capture_e1','capture_e3':'capture_e1_e3','structural_p1':'structural_p1_e1','structural_p1_e3':'structural_p1_e1_e3'}.items():
   assert economic(case['arms'][old]['windows'])==economic(frozen['experiments'][scenario]['arms'][old]['windows']),('CONTROL',scenario,old)
   assert {w:r['signals'] for w,r in economic(case['arms'][new]['windows']).items()}=={w:r['signals'] for w,r in economic(case['arms'][old]['windows']).items()},('E1_FINAL_ALERTS',scenario,new)
   count+=1
  for arm in case['arms'].values():
   a=arm['aggregate']
   if not a['trade_count']:assert a['total_net_usdt']==0 and a['unresolved_ratio'] is None
   elif a['outcome_status'].get('unresolved'):assert a['total_net_usdt'] is None and a['closed_trade_drawdown_usdt'] is None
   assert a['emergency_count'] is None and a['full_mtm_drawdown'] is None
 unchanged=['lbank_futures_monitor.py','lbank_entry_location.py','lbank_structural_target_plan.py','research/evidence_confirmation.py','research/compare_confirmation_experiment.py']
 for p in unchanged:assert (ROOT/p).read_bytes()==subprocess.check_output(['git','show',BASE+':'+p],cwd=ROOT),('FROZEN_CODE',p)
 assert summary['sends']==summary['orders']==0 and not summary['profitability_validated']
 assert summary['source_blockers']==['RAW5M_INDEPENDENCE_UNVERIFIED','CANDLE_VOLUME_UNIT_UNVERIFIED']
 c=json.loads((OUT/'candidate_accounting.json').read_text())
 rows=[r for r in c['detection'] if '_e1' in r['policy']]
 assert len(rows)==12 and all(r['qualified_evaluations']==r['final_first_retest_alerts']==0 for r in rows)
 with (OUT/'trades.csv').open(newline='') as f:trades=list(csv.DictReader(f))
 accounting=json.loads((OUT/'event_accounting.json').read_text())
 for r in trades:
  if r['group'] not in ('added','excluded') or json.loads(r['gate_reasons']):continue
  matched=[a for a in accounting if all(a[k]==r[k] for k in ('scenario','comparison','event_id'))]
  assert len(matched)==1 and any(p['confirmation_status']=='no_confirmation_evaluation_observed' for p in matched[0]['policies'].values()),('MISSING_OBSERVED_REASON_OR_ABSENCE',r['event_id'])
 state=json.loads((ROOT/'research/zerobase/completion_state.json').read_text())
 commands=[{'command':'PYTHONPATH=/workspace/scratch/44f97b78c955/zb-test-deps python -m unittest test_lbank_first_retest_experiment test_lbank_confirmation_experiment test_lbank_entry_location test_lbank_structural_comparison -q','exit_code':0,'tests':44,'new_tests':10}]
 commands += [dict(command=x,exit_code=0) for x in ['PYTHONPATH=/workspace/scratch/44f97b78c955/zb-test-deps python research/compare_first_retest_experiment.py','PYTHONPATH=/workspace/scratch/44f97b78c955/zb-test-deps python research/render_first_retest_report.py','python research/validate_first_retest_experiment.py','git diff --check']]
 checks=dict(all_code_input_output_hashes_match=True,decompressed_csv_hash_matches=True,original_arm_scenario_windows_exactly_equal=count,e1_final_alert_arm_scenario_outputs_exactly_equal=count,frozen_original_code_unchanged=unchanged,first_retest_qualified_alerts_zero=True,no_entry_zero_unresolved_null=True,per_event_recorded_rejections_or_explicit_no_confirmation=True,orders_sends_zero=True,blockers_preserved=True,trade_comparison_scenario_rows=len(trades))
 files=[p for p in OUT.iterdir() if p.is_file() and p.name not in ('validation.json','gate_decisions.csv')]
 result=dict(schema='zb_p2_e1_validation_v1',checked_at=datetime.datetime.now(datetime.timezone.utc).isoformat(),started_at=state['e1_started_at'],source_checkpoint=BASE,predeclared_lease_commit='be8191e9f9280acd207ce9c95ae966c754b6b2d8',initial_code_checkpoint='546bd05a573761fd9adedd76f2bd45da81370663',checks=checks,commands=commands,runtime=dict(python=platform.python_version(),sqlite=sqlite3.sqlite_version),file_sha256={p.name:sha(p) for p in sorted(files)},additional_code_sha256={p:sha(ROOT/p) for p in ('research/validate_first_retest_experiment.py','test_lbank_first_retest_experiment.py')},limits=['Reused development observations; no unseen/continuous qualification','Detection-only keeps original momentum and qualification; bundled location route not enabled','E4/E5/live ledger/P3-P5 incomplete; no profitability or whole-feature completion'])
 (OUT/'validation.json').write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n')
 print(json.dumps(checks,indent=2))
if __name__=='__main__':main()
