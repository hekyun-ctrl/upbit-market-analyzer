"""Paired descriptive screen of pre-frozen target_conflict_no_direction_override_v1."""
from pathlib import Path
import json,hashlib,math
from collections import Counter
from decimal import Decimal
import numpy as np
P=Path(__file__).parent
def read(n,sha):
 b=(P/n).read_bytes();assert hashlib.sha256(b).hexdigest()==sha;return json.loads(b)
a=read('attribution_result.json','057735b5c5a83fac3dfc2edea1b855103131a16e39e643630fcf111db9172802')
r=read('intrabar_result.json','de55cbb223ede9c032aafb2668212004a5c5569cd1ffb2411895e2c8703776a5')
old=read('prior_report.json','c15624c575ff1a08b2fe74761dc9877c5c7bbb104e193667e7845946d35491e7')
accept={x['reference']:x['room']['target_room']!='conflict' for x in a['rows']}
assert len(accept)==22 and sum(accept.values())==9
start=1736294400;days=7
def metrics(rows):
 done=[v for v in rows if v['net_R_1'] is not None]
 xs=[v['net_R_1'] for v in sorted(done,key=lambda z:z['closed'])]
 win=sum(x>0 for x in xs);gain=sum(max(x,0) for x in xs);loss=-sum(min(x,0) for x in xs)
 equity=peak=dd=0
 for x in xs:equity+=x;peak=max(peak,equity);dd=max(dd,peak-equity)
 n=len(rows);tp=sum(v['stage'] for v in rows);z=1.96;ph=tp/n if n else 0
 lower=(ph+z*z/(2*n)-z*math.sqrt(ph*(1-ph)/n+z*z/(4*n*n)))/(1+z*z/n) if n else None
 occupied=len({int((v['opened']-start)//86400) for v in rows})
 return dict(n=n,complete=len(done),unknown=n-len(done),sum_R=sum(xs),mean_R=sum(xs)/len(xs) if xs else None,PF=gain/loss if loss else None,wins=win,losses=sum(x<0 for x in xs),doublecost_R=sum(v['net_R_2'] for v in done),DD_R=dd,TP1=tp,TP1_Wilson_lower=lower,selected_entries_per_day=n/days,selected_opportunity_days=occupied,observed_calendar_days=days,statuses=dict(Counter(v['status'] for v in rows)))
result={}
for path,data in r['paths'].items():
 result[path]={}
 for policy in ('levels','warning_exit','trend_runner'):
  rows=[v for v in data['rows'] if v['policy']==policy];assert len(rows)==22
  keep=[v for v in rows if accept[int(v['id'].rsplit(':',1)[1])]];removed=[v for v in rows if not accept[int(v['id'].rsplit(':',1)[1])]]
  baseline=metrics(rows);ret=metrics(keep);rem=metrics(removed)
  assert abs(baseline['sum_R']-ret['sum_R']-rem['sum_R'])<1e-10
  assert abs(float(sum((Decimal(str(v['net_R_1'])) for v in rows),Decimal(0)))-baseline['sum_R'])<1e-10
  daily=np.zeros((days,2))
  for v in rows:
   d=int((v['opened']-start)//86400);assert 0<=d<days
   daily[d,0]+=v['net_R_1']
   if accept[int(v['id'].rsplit(':',1)[1])]:daily[d,1]+=v['net_R_1']
  # Correlated-day circular blocks; tiny reused development sample, no certification.
  rng=np.random.default_rng(2329);boot=[]
  for _ in range(5000):
   ix=[]
   while len(ix)<days:
    b=int(rng.integers(days));ix.extend([b,(b+1)%days])
   sample=daily[ix[:days]]
   boot.append(float((sample[:,1]-sample[:,0]).sum()))
  urgent={v['reference'] for v in data['events'] if v['reason'] in ('stop_breached','early_invalidation') and 0<=v['at']-v['sent_at']<=300}
  result[path][policy]=dict(baseline=baseline,retained=ret,removed=rem,paired_total_R_change=ret['sum_R']-baseline['sum_R'],paired_dayblock95_total_R_change=list(np.quantile(boot,[.025,.975])),urgent5_proxy_references=sorted(urgent),retained_urgent5_proxy_count=sum(accept[x] for x in urgent),all_rows=[dict(reference=int(v['id'].rsplit(':',1)[1]),retained=accept[int(v['id'].rsplit(':',1)[1])],status=v['status'],net_R=v['net_R_1'],doublecost_R=v['net_R_2'],TP1_state=v['stage']) for v in rows])
unknown={}
for p in ('levels','warning_exit','trend_runner'):
 rs=[v for v in old['rows'] if v['policy']==p];unknown[p]={}
 for name,vs in [('retained',[v for v in rs if accept[int(v['id'].rsplit(':',1)[1])]]),('removed',[v for v in rs if not accept[int(v['id'].rsplit(':',1)[1])]])]:
  unknown[p][name]=dict(n=len(vs),censored=sum(v['status'].startswith('censored_') for v in vs),open=sum(v['status']=='open' for v in vs),references_unknown=[v['id'] for v in vs if v['complete_net_R'] is None])
out=dict(version='target_conflict_no_direction_override_v1',rule_frozen_doc_commit='1508c71afb24f8417df8da0b2da65ddab549a261',source_commit='5c977a7f51cea67048f95791d1dcc4230634939a',results=result,original_observation_unknowns=unknown,retained_references=[x for x in accept if accept[x]],removed_references=[x for x in accept if not accept[x]],new_backtest=False,selected_fixed_entry_subset_only=True,actual_LBank=False,independent_OOS=False,actual_emergency_rate=False,opportunity_recall_known=False,production_changed=False,promotion=False)
(P/'conflict_screen_result.json').write_text(json.dumps(out,separators=(',',':'))+'\n')
print(json.dumps({k:{p:{'retained':v['retained'],'removed_wins':v['removed']['wins'],'change':v['paired_total_R_change'],'CI':v['paired_dayblock95_total_R_change']} for p,v in x.items()} for k,x in result.items()}))
