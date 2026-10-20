"""Independent Decimal checks of diagnostic rejection predicates, not cash returns."""
import json,hashlib
from decimal import Decimal as D
from collections import Counter
from pathlib import Path
x=json.loads(Path('attribution_result.json').read_text())
prior=json.loads(Path('prior_paired_report.json').read_text())['reports']['bb_available_same_gates']['unique_raw_events']
checks=0;lines=Counter()
for id,e in x['events'].items():
 assert sorted(e['selected_reasons'])==sorted(prior[id]['reasons']);checks+=1
for r in x['observations']:
 v=r['plan_diagnostic']['values'];line=r['plan_diagnostic']['return_line'];lines[line]+=1
 assert not r['plan_diagnostic']['plan_returned'];checks+=1
 assert r['available_at']<=r['at']<=r['expires_at'];checks+=1
 assert abs(D(str(r['signed_rsi_delta']))-D(r['direction'])*(D(str(r['rsi_current']))-D(str(r['rsi_previous']))))<D('1e-12');checks+=1
 if line==431:
  assert abs(D(str(v['t1']))-D(str(v['curr'])))>D('1.5')*D(str(v['observed_range']))
  assert not v['ranked']['atr_target_exception'];checks+=2
 elif line==438:
  assert D(r['direction'])*(D(str(v['barrier']))-D(str(v['t1'])))<D(str(v['curr']))*D('.00015');checks+=1
 elif line==447:
  assert v['native_target_room']['target_room']=='conflict'
  assert not v['ranked']['may_override_single_pivots'];checks+=2
 else:raise AssertionError(line)
 assert (r['selected_timing_bypass_reason']=='risk_or_target') == (r['context'][0] and r['signed_rsi_delta']>0 and r['price_deviation']<=.001);checks+=1
out=dict(verified=True,checks=checks,events=len(x['events']),observations=len(x['observations']),
 rejection_line_counts=dict(lines),result_SHA256=hashlib.sha256(Path('attribution_result.json').read_bytes()).hexdigest(),
 scope='Exact prior six-event reason correspondence and Decimal rejection inequalities; no financial returns, trades or alpha test')
Path('verification.json').write_text(json.dumps(out,indent=2)+'\n')
print(json.dumps(out))
