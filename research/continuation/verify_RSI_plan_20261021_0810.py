"""Independent Decimal verification of twenty planning rejection/feasibility records."""
import json,hashlib
from decimal import Decimal as D
from collections import Counter
from pathlib import Path
r=json.loads(Path('RSI_plan_result.json').read_text());checks=0;feasible=[]
for x in r['calls']:
 v=x['trace']['values'];d=D(x['direction']);en=D(str(x['price']));stop=D(str(v['stop']))
 line=x['trace']['return_line'];assert not x['plan_returned'];checks+=1
 if line==412:
  distance=abs(en-stop)/en
  assert distance>D('.006') or distance<D(str(v['minimum_stop']))-D('1e-10');checks+=1
 elif line==431:
  assert abs(D(str(v['t1']))-en)>D('1.5')*D(str(v['observed_range']))
  assert not v['ranked']['atr_target_exception'];checks+=2
 elif line==438:
  assert d*(D(str(v['barrier']))-D(str(v['t1'])))<en*D('.00015');checks+=1
 else:raise AssertionError(line)
 for key,s in x['plan_cost_sensitivity'].items():
  k=D(key);bar=D(str(x['barrier_price']))
  stopfill=stop*(1-d*D('.0002')*k)
  loss=-(d*(stopfill-en)-D('.0006')*k*(en+stopfill)-en*D('.0001')*k)
  net=d*(bar-en)-D('.0006')*k*(en+bar)-en*D('.0001')*k
  assert abs(float(net/loss)-s['maximum_R_at_barrier'])<1e-10;checks+=1
  assert net/loss<D('1.5');checks+=1
  if key=='1':feasible.append(float(net/loss))
assert len(r['calls'])==20 and len({x['id'] for x in r['calls']})==15;checks+=2
out=dict(checks=checks,actual_calls=20,unique_candidate_ids=15,
 return_counts=dict(Counter(str(x['trace']['return_line']) for x in r['calls'])),
 ahead_barrier_cases=len(feasible),behind_barrier_cases=sum(x['directed_barrier_room'] is not None and x['directed_barrier_room']<=0 for x in r['calls']),
 maximum_base_plan_R=max(feasible),maximum_doublecost_plan_R=max(x['plan_cost_sensitivity']['2']['maximum_R_at_barrier'] for x in r['calls'] if x['plan_cost_sensitivity']),
 feasible_R_atleast_1_5=0,result_SHA256=hashlib.sha256(Path('RSI_plan_result.json').read_bytes()).hexdigest(),
 scope='Planning-model inequalities and cost feasibility, NOT closed-trade performance or native fills',
 verified=True)
Path('RSI_plan_verification.json').write_text(json.dumps(out,indent=2)+'\n');print(json.dumps(out))
