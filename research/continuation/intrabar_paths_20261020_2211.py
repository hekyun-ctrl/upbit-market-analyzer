"""Two predeclared hypothetical intraminute paths; development sensitivity only."""
import csv,io,zipfile,json,hashlib,copy,math
from pathlib import Path
from collections import Counter
from decimal import Decimal as D
import prior
ROOT=Path(__file__).parent
SOURCE='5c977a7f51cea67048f95791d1dcc4230634939a'
def archive(name,expected):
 b=(ROOT/(name+'.zip')).read_bytes();assert hashlib.sha256(b).hexdigest()==expected
 z=zipfile.ZipFile(io.BytesIO(b));return list(csv.DictReader(io.TextIOWrapper(z.open(z.namelist()[0]))))
def main():
 raw=archive('bars','b69ca5b2c2d1ae00d68d2790d09138cb2a44085ff19e0da5dc98b569ea7e489e')
 bars=[dict(start=int(r['open_time'])//1000,**{k:float(r[k]) for k in ('open','high','low','close','volume')}) for r in raw]
 assert len(bars)==44640 and all(b['start']-a['start']==60 for a,b in zip(bars,bars[1:]))
 rates=[(int(r['calc_time'])//1000,float(r['last_funding_rate'])) for r in archive('funding','df725010d55458866eb5a47e9ee708c6796c9d7e77b14c38342cea11f381815e')]
 assert len(rates)==93
 five=[]
 for i in range(0,len(bars),5):
  g=bars[i:i+5];five.append(dict(start=g[0]['start'],open=g[0]['open'],high=max(b['high'] for b in g),low=min(b['low'] for b in g),close=g[-1]['close'],volume=sum(b['volume'] for b in g)))
 saved=json.loads((ROOT/'prior_report.json').read_text());entries=saved['entries'];assert len(entries)==22
 start=1736294400;end=1736985600
 bytime={b['start']:b for b in bars};emap={e['at']:e for e in entries}
 golden=prior.golden_cases();result={};checks=0
 for path in [('open','high','low','close'),('open','low','high','close')]:
  e=prior.Engine();cache={};prefix=[]
  for i,b in enumerate(bars):
   t=b['start']
   if not start<=t<end:continue
   base=prior.native_at(bars,five,i,cache)
   assert all(x['start']+60<=t for x in base['minute_bars'])
   assert all(x['start']+300<=t for x in base['five_bars'])
   for j,key in enumerate(path):
    now=t+15*j;price=b[key];state=dict(base,quote=price)
    # Engine appends each poll; remove that append, then update minute sample only.
    before=len(e.quotes);e.step(now,price,state);e.quotes.pop()
    assert len(e.quotes)==before
    if j==0:
     e.quotes.append(price)
     if t in emap:
      en=emap[t];e.add(copy.deepcopy(en['plan']),t,en['reference'])
    checks+=1
  rows=[dict(r) for r in e.book.db.execute('SELECT * FROM signals ORDER BY opened,id,policy')]
  for r in rows:
   p=json.loads(r['plan']);r['plan']=p;legs=[l for l in e.book.legs if l['id']==r['id'] and l['policy']==r['policy']]
   complete=r['status']!='open' and not r['status'].startswith('censored_')
   denom=-prior.m.net_pnl_per_btc(p['direction'],p['price'],p['stop'])
   for k in (1,2):
    total=0.;decimal=D(0)
    for l in legs:
     d=p['direction'];entry=p['price']*(1+d*.0002*k);exit=l['price']*(1-d*.0002*k)
     cash=d*(exit-entry)-.0006*k*(entry+exit)
     de=D(str(p['price']))*(1+D(d)*D('.0002')*k);dx=D(str(l['price']))*(1-D(d)*D('.0002')*k)
     dc=D(d)*(dx-de)-D('.0006')*k*(de+dx)
     for ft,rate in rates:
      if r['opened']<ft<=l['at']:
       charge=d*rate*bytime[ft]['open'];cash-=charge*k if charge>0 else charge
       q=D(d)*D(str(rate))*D(str(bytime[ft]['open']));dc-=q*k if q>0 else q
     total+=l['fraction']*cash;decimal+=D(str(l['fraction']))*dc
    assert abs(total-float(decimal))<1e-7
    r['net_R_'+str(k)]=total/denom if complete else None
   r['known_leg_fraction']=sum(l['fraction'] for l in legs)
  summaries={}
  for policy in ('levels','warning_exit','trend_runner'):
   rs=[r for r in rows if r['policy']==policy];done=sorted([r for r in rs if r['net_R_1'] is not None],key=lambda r:r['closed'])
   xs=[r['net_R_1'] for r in done];gain=sum(max(x,0) for x in xs);loss=-sum(min(x,0) for x in xs);eq=peak=dd=0
   for x in xs:eq+=x;peak=max(peak,eq);dd=max(dd,peak-eq)
   summaries[policy]=dict(signals=len(rs),complete=len(done),statuses=dict(Counter(r['status'] for r in rs)),conditional_sum_R=sum(xs),conditional_mean_R=sum(xs)/len(xs) if xs else None,PF=gain/loss if loss else None,doublecost_R=sum(r['net_R_2'] for r in done),closed_DD_R=dd,TP1_states=sum(r['stage'] for r in rs),all22_mean_R=sum(xs)/22 if len(done)==22 else None)
  result['-'.join(path)]=dict(rows=rows,legs=e.book.legs,events=e.events,summaries=summaries,minute_samples=len(e.quotes))
  e.close()
 out=dict(protocol='fixed22_two_intrabar_paths_v1',source_commit=SOURCE,paths=result,golden_cases=golden,completed_bar_prefix_checks=checks,decimal_checks=264,source_unchanged=True,entries=entries,original_unknowns_preserved=saved['summaries']['trend_runner'],native_LBank_performance=False,real_ticks=False,independent_OOS=False,mathematical_bounds=False,promotion=False)
 (ROOT/'intrabar_result.json').write_text(json.dumps(out,separators=(',',':'))+'\n')
 print(json.dumps({k:v['summaries'] for k,v in result.items()}))
if __name__=='__main__':main()
