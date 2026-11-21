"""Frozen Jan 8-15 2025 development replay, Binance USD-M proxy ONLY.
No native LBank performance claim, no transport/collector/optimizer/orders.
python research/zerobase/compare_20261022.py --data DIR --output DIR
Input: btc202501.zip, BTCUSDT-5m-2025-01.zip, BTCUSDT-fundingRate-2025-01.zip
"""
import argparse,csv,io,zipfile,json,hashlib,sys,types,logging,dataclasses,math
from pathlib import Path
from datetime import datetime,timezone
from collections import Counter
ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT));sys.path.insert(0,str(Path(__file__).parent))
# Offline only: ccxt is never used by classify/confirm/accounting functions.
sys.modules.setdefault('ccxt',types.ModuleType('ccxt'))
import frozen_entry as old
import frozen_management as management
from lbank_zb.engine import Engine
from lbank_zb.core import Bar,Costs,D,encode,digest,aggregate
from lbank_zb.evaluation import paired_daily_difference

def read(path):
 with zipfile.ZipFile(path) as z:return list(csv.DictReader(io.TextIOWrapper(z.open(z.namelist()[0]))))
def timestamp(s):return int(datetime.fromisoformat(s).replace(tzinfo=timezone.utc).timestamp())
START=timestamp('2025-01-08');STOP=timestamp('2025-01-15');END=timestamp('2025-01-16')
def load(data):
 names=['btc202501.zip','BTCUSDT-5m-2025-01.zip','BTCUSDT-fundingRate-2025-01.zip']
 raws=[read(data/n) for n in names];manifest=[]
 for n,rows in zip(names,raws):manifest.append(dict(file=n,SHA256=hashlib.sha256((data/n).read_bytes()).hexdigest(),rows=len(rows)))
 def bars(rows,p):
  result=[]
  for r in rows:
   t=int(r['open_time'])//1000
   result.append(dict(period=p,start=t,open=float(r['open']),high=float(r['high']),low=float(r['low']),close=float(r['close']),volume=float(r['volume']),turnover=float(r['quote_volume']),updated=t+p-1,quality='stream_closed'))
  assert all(b['start']-a['start']==p for a,b in zip(result,result[1:]))
  assert all(b['low']<=min(b['open'],b['close'])<=max(b['open'],b['close'])<=b['high'] for b in result)
  return result
 one,five=bars(raws[0],60),bars(raws[1],300)
 rates=[(int(r['calc_time'])//1000,D(r['last_funding_rate'])) for r in raws[2]]
 return one,five,rates,manifest
def day(t):return (int(t)+32400)//86400
def stats(rows):
 done=[r for r in rows if r.get('net') is not None];v=[float(r['net']) for r in sorted(done,key=lambda r:r['exit_at'])]
 gain=sum(max(0,x) for x in v);loss=-sum(min(0,x) for x in v);eq=peak=dd=0
 for x in v:eq+=x;peak=max(peak,eq);dd=max(dd,peak-eq)
 return dict(entries=len(rows),closed=len(done),censored_or_open=len(rows)-len(done),net_USDT=sum(v),PF=gain/loss if loss else None,mean_net=sum(v)/len(v) if v else None,realized_drawdown_USDT=dd,TP1_states=sum(bool(r.get('partial')) for r in rows),doublecost_net_USDT=sum(float(r['double_net']) for r in done),statuses=dict(Counter(r['status'] for r in rows)))
def main():
 ap=argparse.ArgumentParser();ap.add_argument('--data',type=Path,required=True);ap.add_argument('--output',type=Path,required=True);args=ap.parse_args();args.output.mkdir(parents=True,exist_ok=True)
 logging.disable(logging.CRITICAL);one,five,rates,manifest=load(args.data);bytime={b['start']:b for b in one}
 old.load=lambda *a:(one,five,[(t,float(r)) for t,r in rates],manifest)
 captured=[];original=old.m.attach_thesis
 class Done(BaseException):pass
 def capture(p,s,t):
  if captured and t<captured[-1]['at']:raise Done()
  q=original(p,s,t);captured.append(dict(at=t,plan=management.profit_plan(q)));return q
 old.m.attach_thesis=capture
 saved=sys.argv
 try:
  sys.argv=['offline','--data',str(args.data),'--month','2025-01','--start','2025-01-08T00:00:00Z','--end','2025-01-15T00:00:00Z','--output',str(args.output/'entry_diagnostic.json')]
  try:old.main()
  except Done:pass
 finally:old.m.attach_thesis=original;sys.argv=saved
 print('baseline_entries',len(captured),flush=True)
 baseline=management.Engine();cache={};entrymap={r['at']:r for r in captured}
 for i,b in enumerate(one):
  now=b['start']
  if not START<=now<END:continue
  baseline.step(now,b['open'],management.native_at(one,five,i,cache))
  if now in entrymap:baseline.add(entrymap[now]['plan'],now)
 rows=[]
 for raw in baseline.book.db.execute("SELECT * FROM signals WHERE policy='trend_runner' ORDER BY opened"):
  r=dict(raw);p=json.loads(r['plan']);d=p['direction'];entry=p['price'];denom=-old.m.net_pnl_per_btc(d,entry,p['stop']);q=2.5/denom
  legs=[l for l in baseline.book.legs if l['id']==r['id'] and l['policy']=='trend_runner'];fraction=sum(l['fraction'] for l in legs)
  complete=r['status']!='open' and not r['status'].startswith('censored_') and abs(fraction-1)<1e-9
  cash=[]
  for mult in (1,2):
   net=0
   for l in legs:
    ei=entry*(1+d*.0002*mult);xo=l['price']*(1-d*.0002*mult);unit=d*(xo-ei)-.0006*mult*(ei+xo)
    for ft,rate in rates:
     if r['opened']<ft<=l['at']:
      charge=d*float(rate)*bytime[ft-ft%60]['open'];unit-=charge*mult if charge>0 else charge
    net+=q*l['fraction']*unit
   cash.append(net)
  rows.append(dict(system='existing',id=r['id'],entry_at=r['opened'],exit_at=r['closed'],direction=d,strategy=p['strategy'],entry=entry,stop=p['stop'],TP1=p['target1'],partial=bool(r['stage']),status=r['status'],quantity=q,net=cash[0] if complete else None,double_net=cash[1] if complete else None,known_leg_net=cash[0],legs=legs,limitation='existing plan at signal quote; 60s sampled management; overlapping references preserved; funding censor retained'))
 baseline.close();print('baseline_closed',stats(rows),flush=True)
 (args.output/'existing_rows.json').write_text(json.dumps(rows,ensure_ascii=False,indent=2)+'\n')
 # Research-only proxy adapter: internal schema is LBank-shaped; explicit source
 # below prevents treating foreign candles as native or certifying production.
 e=Engine(Costs(D('.0006'),D('.0006'),D(2),D('.1'),D('.001'),D(5),D(0),version='DECLARED_PROXY_ASSUMPTIONS'))
 e.seen=set();fmap={b['start']:b for b in five};fund=dict(rates);crosschecks=0;mismatches=[]
 def bar(b):return Bar(b['start'],b['period'],*[D(str(b[k])) for k in ('open','high','low','close','volume')],b['start']+b['period'],source='BINANCE_USDM_PROXY_NOT_NATIVE_LBANK')
 for i,b in enumerate(one):
  t=b['start'];end=t+60
  if not START-3*86400<=t<END:continue
  prior=[abs(r) for ft,r in rates if ft<t];reserve=D(str(b['open']))*max(prior or [D('.0001')])
  e.costs=dataclasses.replace(e.costs,funding_per_btc=reserve)
  if t in fund and e.position:e.position.funding_event(t,fund[t],D(str(b['open'])))
  # Warmup can detect but cannot establish pre-window positions.
  if t<START:e.pending=[]
  if t>=STOP:e.pending=[]
  e.ingest(bar(b),end)
  if end%300==0:
   fb=fmap[end-300];ag=aggregate(e.one[-5:],300)[0];bb=bar(fb)
   price_ok=all(abs(getattr(ag,k)-getattr(bb,k))<=D('.1') for k in ('o','h','l','c'))
   volume_ok=abs(ag.v-bb.v)<=D('.00000001');crosschecks+=1
   if not price_ok or not volume_ok:mismatches.append(dict(start=fb['start'],minute_aggregate=dataclasses.asdict(ag),published5m=dataclasses.asdict(bb)))
   # Decimal aggregation avoids binary-float volume conversion mismatch.
   if volume_ok:bb=dataclasses.replace(bb,v=ag.v)
   e.ingest(bb,end)
  if t<START or t>=STOP:e.pending=[]
  if (i%1440)==0:print('ZB_day',datetime.fromtimestamp(t,timezone.utc).date(),flush=True)
 new=[]
 for pos in e.closed+([e.position] if e.position else []):
  if pos.fill_time<START:continue
  p=pos.plan;complete=pos.state=='PAPER_CLOSED' and not pos.health;exit_at=max((ev['time'] for ev in pos.events),default=pos.fill_time) if complete else None
  # Fixed position path stress only, not a rerun with retuned gates.
  extra=p.quantity*p.entry*p.costs.fee_entry+sum(D(ev['quantity'])*D(ev['price'])*p.costs.fee_exit for ev in pos.events if 'quantity' in ev)
  extra+=p.quantity*p.entry*D('.0002')+sum(D(ev['quantity'])*D(ev['price'])*D('.0002') for ev in pos.events if 'quantity' in ev)
  extra+=sum(max(D(ev['paid']),D(0)) for ev in pos.events if ev['kind']=='FUNDING')
  new.append(dict(system='ZB',id=p.id,entry_at=pos.fill_time,exit_at=exit_at,direction=p.candidate.side,strategy=p.candidate.strategy,entry=str(p.entry),stop=str(p.stop),TP1=str(p.target),partial=pos.partial,status=pos.state if not pos.health else ','.join(sorted(pos.health)),quantity=str(p.quantity),net=str(pos.cash) if complete else None,double_net=str(pos.cash-extra) if complete else None,known_leg_net=str(pos.cash),legs=pos.events,limitation='double cost is fixed-path stress; scenario assumptions; proxy only'))
 daily={d:D(0) for d in range(day(START),day(END-1)+1)}
 def totals(rs):
  out=dict(daily)
  for r in rs:
   if r['net'] is not None:out[day(r['exit_at'])]+=D(str(r['net']))
  return out
 out=dict(protocol='same_data_existing_ZB_development_v1',entry_window_UTC=['2025-01-08','2025-01-15 exclusive'],followup_end_UTC='2025-01-16 exclusive',historical_seen=True,native_LBank_performance=False,independent_holdout=False,messages_sent=0,orders=0,input_manifest=manifest,crosschecked_5m=crosschecks,existing=stats(rows),ZB=stats(new),paired_realized_daily_difference=paired_daily_difference(totals(new),totals(rows)),ZB_event_counts=e.counts,ZB_health=sorted(e.health),rows=rows+new,limitations=['Binance proxy, not LBank fills or latency','Existing management sampled at minute opens; intraminute events censored by existing engine','Existing entry at alert quote; ZB uses design 60-second latency; no common entry-latency proof','Declared same 6bp fee and 2bp slip; tick/step/minimum-notional assumptions not LBank verified','Risk normalization: existing fixed2.5USDT planned loss per reference; ZB design0.25% current model equity; overlap preserved, not matched portfolio risk','Funding actual Binance settlement history; ZB planning reserve uses only prior rates, first7days history not30days','No-trade/censored rows are not wins; open positions never forced closed','Double-cost figure fixed-path repricing, not full doubled-cost gate rerun','3day block bootstrap on realized cash only; excludes unresolved; insufficient for promotion'],promotion=False)
 (args.output/'comparison.json').write_text(json.dumps(out,default=encode,ensure_ascii=False,indent=2)+'\n')
 (args.output/'crosscheck_mismatches.json').write_text(json.dumps(mismatches,default=encode,ensure_ascii=False,indent=2)+'\n')
 (args.output/'zb_events.json').write_text(json.dumps(e.events,default=encode,ensure_ascii=False,indent=2)+'\n')
 with (args.output/'trades.csv').open('w') as f:
  keys=['system','id','entry_at','exit_at','direction','strategy','entry','stop','TP1','partial','status','quantity','net','double_net'];w=csv.DictWriter(f,keys,extrasaction='ignore');w.writeheader();w.writerows(rows+new)
 print(json.dumps({k:v for k,v in out.items() if k in ('existing','ZB','paired_realized_daily_difference')},default=encode),flush=True)
if __name__=='__main__':main()
