"""Unchanged RSI chronological transfer replay; not native execution or new OOS."""
import argparse,csv,io,zipfile,json,hashlib,logging,math
from pathlib import Path
from collections import Counter
import lbank_futures_monitor as m
from lbank_rsi_divergence import DivergenceStudy,analyze
class TechniqueStudy(DivergenceStudy):
 def step(self,now,price,state,calculate_plan,context_gate,*,baseline_reason):
  setups={e['id']:e['strategy'] for e in analyze(state)['setups']}
  r=super().step(now,price,state,calculate_plan,context_gate,baseline_reason=baseline_reason)
  for d in r['decisions']:d.setdefault('strategy',setups.get(d['id'],'unknown'))
  return r
from lbank_native_entry import SOURCE,wilder_rsi_series
from lbank_trend_runner import valid_state
from prior import AuditBook
def readzip(path,sha):
 b=path.read_bytes();assert hashlib.sha256(b).hexdigest()==sha
 z=zipfile.ZipFile(io.BytesIO(b));return list(csv.DictReader(io.TextIOWrapper(z.open(z.namelist()[0]))))
def main():
 ap=argparse.ArgumentParser();ap.add_argument('--data',required=True);ap.add_argument('--output',required=True);args=ap.parse_args()
 logging.disable(logging.CRITICAL);root=Path(args.data);out=Path(args.output)
 bars=[dict(start=int(r['open_time'])//1000,quality='stream_closed',**{k:float(r[k]) for k in ('open','high','low','close','volume')}) for r in readzip(root/'bars.zip','b69ca5b2c2d1ae00d68d2790d09138cb2a44085ff19e0da5dc98b569ea7e489e')]
 assert len(bars)==44640 and all(b['start']-a['start']==60 for a,b in zip(bars,bars[1:]))
 five=[]
 for i in range(0,len(bars),5):
  g=bars[i:i+5];five.append(dict(start=g[0]['start'],quality='stream_closed',open=g[0]['open'],high=max(b['high'] for b in g),low=min(b['low'] for b in g),close=g[-1]['close'],volume=sum(b['volume'] for b in g)))
 rates=[(int(r['calc_time'])//1000,float(r['last_funding_rate'])) for r in readzip(root/'funding.zip','df725010d55458866eb5a47e9ee708c6796c9d7e77b14c38342cea11f381815e')];assert len(rates)==93
 bytime={b['start']:b for b in bars}
 study=TechniqueStudy(':memory:',m.REF_BALANCE);study.book.db.close();study.book=AuditBook()
 study.book.db.execute('CREATE TABLE rsi_candidates(id TEXT PRIMARY KEY,first_at REAL,price REAL,status TEXT,detail TEXT)')
 start=1736294400;end=1736899200;followup=1736985600
 cache={};decisions=Counter();unique={};events=[];polls=0;ready=0
 for i,b in enumerate(bars):
  now=b['start']
  if not start<=now<followup:continue
  j=i//5
  if cache.get('j')!=j:
   fb=five[max(0,j-250):j];mp={x['start']:x for x in fb};last15=now//900*900-900
   closes=[x['close'] for x in fb]
   cache=dict(j=j,base=dict(source=SOURCE,five_bars=fb,research_five_bars=five[max(0,j-300):j],last5_start=fb[-1]['start'],closes15=[mp[t+600]['close'] for t in range(last15-3600,last15+1,900)],rsi5=dict(closes=closes,rsi=wilder_rsi_series(closes))))
  minute=bars[i-360:i];state=dict(cache['base'],minute_bars=minute,prices=[x['close'] for x in minute],quote=b['open'])
  assert valid_state(state,now) and all(x['start']+60<=now for x in minute) and all(x['start']+300<=now for x in state['research_five_bars'])
  polls+=1
  if now<end:
   result=study.step(now,b['open'],state,m.calculate_plan,m.native_context_gate,baseline_reason='offline_transfer_not_live_baseline_replay')
   ready+=int(result['ready'])
   for d in result['decisions']:
    decisions[d['reason']]+=1;unique.setdefault(d['id'],dict(first_at=now,strategy=d['strategy'],reasons=[]))
    if d['reason'] not in unique[d['id']]['reasons']:unique[d['id']]['reasons'].append(d['reason'])
    if d['reason'] in ('paper_opened','paper_expired'):events.append(dict(at=now,**d))
  else:study.book.observe(now,b['open']);study.book.sample(now,b['open'])
  if now%86400==0:
   out.with_suffix('.checkpoint.json').write_text(json.dumps(dict(through=now,polls=polls,unique=len(unique),decisions=dict(decisions)))+'\n')
 rows=[dict(x) for x in study.book.db.execute('SELECT * FROM signals ORDER BY opened,id,policy')]
 for row in rows:
  p=json.loads(row['plan']);row['plan']=p;ls=[x for x in study.book.legs if x['id']==row['id'] and x['policy']==row['policy']]
  complete=row['status']!='open' and not row['status'].startswith('censored_')
  denom=-m.net_pnl_per_btc(p['direction'],p['price'],p['stop'])
  for k in (1,2):
   cash=0
   for l in ls:
    d=p['direction'];en=p['price']*(1+d*.0002*k);ex=l['price']*(1-d*.0002*k)
    v=d*(ex-en)-.0006*k*(en+ex)
    for ft,rate in rates:
     if row['opened']<ft<=l['at']:
      q=d*rate*bytime[ft]['open'];v-=q*k if q>0 else q
    cash+=l['fraction']*v
   row['complete_R_'+str(k)]=cash/denom if complete else None
  row['known_leg_fraction']=sum(l['fraction'] for l in ls)
 summaries={}
 for strategy in ('rsi_divergence','rsi_pullback'):
  summaries[strategy]={}
  for policy in ('levels','warning_exit','trend_runner'):
   selected=[r for r in rows if r['plan']['strategy']==strategy and r['policy']==policy];done=[r for r in selected if r['complete_R_1'] is not None];xs=[r['complete_R_1'] for r in sorted(done,key=lambda r:r['closed'])]
   gain=sum(max(x,0) for x in xs);loss=-sum(min(x,0) for x in xs);eq=peak=dd=0
   for x in xs:eq+=x;peak=max(peak,eq);dd=max(dd,peak-eq)
   summaries[strategy][policy]=dict(opened=len(selected),complete=len(done),censored=sum(r['status'].startswith('censored_') for r in selected),open_end=sum(r['status']=='open' for r in selected),statuses=dict(Counter(r['status'] for r in selected)),conditional_R=sum(xs),conditional_mean_R=sum(xs)/len(xs) if xs else None,PF=gain/loss if loss else None,doublecost_R=sum(r['complete_R_2'] for r in done),DD_R=dd,TP1_states=sum(r['stage'] for r in selected),selected_opportunity_days=len({int((r['opened']-start)//86400) for r in selected}),entries_per7days=len(selected)/7)
 output=dict(protocol='unchanged_RSI_minute_open_transfer_v1',source_commit='25388b431c9c9d38df6a434670586c65f382d604',entry_start=start,entry_end_exclusive=end,followup_end_exclusive=followup,minute_open_poll_assumption=True,original_live15s_cadence_reproduced=False,prices_source='Binance official UM BTCUSDT Jan2025',native_LBank=False,new_independent_OOS=False,rules_changed=False,promotion=False,messages_sent=0,orders=0,polls=polls,ready_polls=ready,unique_raw_events=unique,decision_occurrences=dict(decisions),event_transitions=events,rows=rows,legs=study.book.legs,summaries=summaries,urgent_warning_rate=None,manual_fills=None,actual_source_warning_tracker_not_in_this_study=True)
 out.write_text(json.dumps(output,separators=(',',':'))+'\n');print(json.dumps(summaries));study.book.db.close()
if __name__=='__main__':main()

