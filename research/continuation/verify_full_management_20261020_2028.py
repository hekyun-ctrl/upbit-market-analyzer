"""Independent Decimal accounting; complete-case uncertainty retains censor limits."""
import argparse,copy,hashlib,importlib.util,json,random
from collections import defaultdict,Counter
from datetime import datetime,timedelta,timezone
from decimal import Decimal,localcontext
from pathlib import Path
ap=argparse.ArgumentParser();ap.add_argument('--result',required=True);ap.add_argument('--replay',required=True);ap.add_argument('--trial',required=True);ap.add_argument('--data',required=True);ap.add_argument('--output',required=True);a=ap.parse_args()
out=json.loads(Path(a.result).read_text())
def module(name,path):
 s=importlib.util.spec_from_file_location(name,path);m=importlib.util.module_from_spec(s);s.loader.exec_module(m);return m
q=module('full_replay_verify',a.replay);trial=q.load_trial(a.trial)
bars,five,rates,objects=trial.load(a.data,'2025-01');marks={b['start']:b['open'] for b in bars}
maxerr=0.;maxsource=0.;decimal_checks=0;source_checks=0
for r in out['rows']:
 p=r['plan'];legs=[l for l in out['legs'] if l['id']==r['id'] and l['policy']==r['policy']]
 with localcontext() as ctx:
  ctx.prec=45;D=lambda x:Decimal(str(x));d=D(p['direction']);entry=D(p['price']);stop=D(p['stop']);fee=D(p['fee']);slip=D(p['slippage']);reserve=D(p['funding_reserve'])
  stop_fill=stop*(1-d*slip);denom=-(d*(stop_fill-entry)-fee*(entry+stop_fill)-reserve*entry)
  for k in (1,2):
   K=D(k);ei=entry*(1+d*D('.0002')*K);net=Decimal(0)
   for leg in legs:
    xo=D(leg['price'])*(1-d*D('.0002')*K);cash=d*(xo-ei)-D('.0006')*K*(ei+xo)
    for ft,rate in rates:
     if r['opened']<ft<=leg['at']:
      charge=d*D(rate)*D(marks[ft]);cash-=charge*K if charge>0 else charge
    net+=D(leg['fraction'])*cash
   key='known_legs_R' if k==1 else 'known_legs_doublecost_R';err=abs(float(net/denom)-r[key]);assert err<1e-9;maxerr=max(maxerr,err);decimal_checks+=1
  if r['net'] is not None:
   expected=Decimal(0)
   for leg in legs:
    exit=D(leg['price'])*(1-d*slip)
    expected+=D(leg['fraction'])*D(p['quantity_btc'])*(d*(exit-entry)-fee*(entry+exit)-reserve*entry)
   err=abs(float(expected)-r['net']);assert err<1e-8;maxsource=max(maxsource,err);source_checks+=1
  else:assert r['status'].startswith('censored_') or r['status']=='open'

start=datetime(2025,1,8,tzinfo=timezone.utc);days=[(start+timedelta(days=i)).date().isoformat() for i in range(7)]
for policy,summary in out['summaries'].items():
 rows=[r for r in out['rows'] if r['policy']==policy];complete=[r for r in rows if r['complete_net_R'] is not None]
 grouped=defaultdict(list)
 for r in complete:grouped[datetime.fromtimestamp(r['opened'],timezone.utc).date().isoformat()].append(r['complete_net_R'])
 rng=random.Random(20261020);means=[]
 for _ in range(5000):
  selected=[]
  while len(selected)<7:
   j=rng.randrange(7);selected.extend([days[j],days[(j+1)%7]])
  xs=[x for day in selected[:7] for x in grouped[day]]
  if xs:means.append(sum(xs)/len(xs))
 means.sort();summary['conditional_complete_2day_block95_mean_R']=[means[int(len(means)*.025)],means[int(len(means)*.975)]] if means else None
 summary['unconditional_all22_net_R']=summary['conditional_complete_sum_R'] if len(complete)==22 else None
 summary['censor_coverage_complete_fraction']=len(complete)/22
 summary['TP1_Wilson_interpretation']='observed model TP1 states / all22 including unknown censors; conservative known-hit fraction, not empirical actual hit-rate CI'

byid=defaultdict(dict)
for r in out['rows']:byid[r['id']][r['policy']]=r
paired=[]
for ident,group in byid.items():
 if all(group[k]['complete_net_R'] is not None for k in ('levels','warning_exit','trend_runner')):
  paired.append(dict(id=ident,levels_R=group['levels']['complete_net_R'],warning_R=group['warning_exit']['complete_net_R'],runner_R=group['trend_runner']['complete_net_R']))
out['paired_all3_complete_descriptive_only']=dict(count=len(paired),selected_on_completion=True,selection_bias_corrected=False,rows=paired)
urgent={}
for e in out['events']:
 if e['reason'] in ('stop_breached','early_invalidation','profit_guard_touched'):urgent.setdefault(e['reference'],e)
out['advisory_diagnostic']=dict(first_actionable_events=len(urgent),within5m=sum(e['at']-e['sent_at']<=300 for e in urgent.values()),reasons=dict(Counter(e['reason'] for e in out['events'])),actual_LBank_5m_urgent_rate=None)
# Fixed historical entry+60 boundaries: future-current HLC mutations leave
# completed state and one existing management step invariant, not full-run proof.
prefix_checks=0
for entry in out['entries']:
 at=entry['at']+60;i=(at-bars[0]['start'])//60;x=q.native_at(bars,five,i,{})
 changed=copy.deepcopy(bars[:i+1]);changed[i].update(high=changed[i]['open']*3,low=1.,close=2.)
 # Build completed5m series solely from unchanged bars prior to this boundary.
 y=q.native_at(changed,five,i,{})
 assert x==y
 ea,eb=q.Engine(),q.Engine();ea.add(entry['plan'],entry['at']);eb.add(entry['plan'],entry['at'])
 ra=ea.step(at,bars[i]['open'],x);rb=eb.step(at,bars[i]['open'],y)
 xa=[dict(r) for r in ea.book.db.execute('SELECT * FROM signals ORDER BY policy')];xb=[dict(r) for r in eb.book.db.execute('SELECT * FROM signals ORDER BY policy')]
 assert ra==rb and xa==xb;ea.close();eb.close();prefix_checks+=1
out['verification']=dict(decimal_policy_cost_checks=decimal_checks,source_model_cash_checks=source_checks,max_R_error=maxerr,max_source_USDT_error=maxsource,one_step_future_HLC_prefix_checks=prefix_checks,full_simulation_future_append_replay=False,original_reconstruction_matches=22,golden_case_count=len(out['golden_cases']),code_sha256=hashlib.sha256(Path(a.replay).read_bytes()).hexdigest(),verifier_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),uncertainty='conditional on uncensored cases, UTC2day circular blocks5000, no selection correction; censoring prevents full22 runner expectation',source_model_funding_reserve_not_same_as_actual_archived_funding=True)
Path(a.output).write_text(json.dumps(out,ensure_ascii=False,indent=2)+'\n');print(json.dumps(out['verification']))
