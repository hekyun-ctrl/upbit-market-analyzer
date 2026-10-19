"""Frozen preplanned-entry hypotheses; exploratory and blind historical checks.

No production/venue substitution. Pending limits require 0.1 ATR penetration;
stop-market gaps use worse opens. Entry-minute target credits are forbidden.
"""
import argparse,json,math
from pathlib import Path
import numpy as np
import pandas as pd
from research.long_history.evaluate import (load_archives,aggregate_minutes,feature_frame,
                                           verify_manifests,unit_net,score,FEE,SLIP,digest)

NAMES=['range_edge_limit','trend_value_limit','preplanned_break_stop']


def prepare(five):
    parts=[]
    for _,s in five.groupby((five['start'].diff()!=300).cumsum()):
        f=feature_frame(s.reset_index(drop=True));c=f['close'];a=f['atr']
        f['fast']=c.ewm(span=9,adjust=False).mean();f['slow']=c.ewm(span=21,adjust=False).mean()
        quarter=f['start']//900*900
        q=f.groupby(quarter).agg(count=('start','count'),close=('close','last'),first=('start','first'),last=('start','last'))
        qc=q['close'].where((q['count']==3)&(q['first']==q.index)&(q['last']==q.index+600));diff=qc.diff()
        direction=pd.Series(np.where((diff>0)&(diff.shift()>0),1,np.where((diff<0)&(diff.shift()<0),-1,0)),index=q.index+900)
        d=direction.reindex(f['start']+300,method='ffill').to_numpy()
        f['dominant']=np.where((d*(f['fast']-f['slow'])>0)&(d*f['fast'].diff()>0),d,0)
        # All levels include only bars that have completed before plan placement.
        f['known_high']=f['high'].rolling(20).max();f['known_low']=f['low'].rolling(20).min()
        f['known_width']=f['known_high']-f['known_low'];f['range_balanced']=(c-c.shift(19)).abs()<=.35*f['known_width']
        f['known_12hi']=f['high'].rolling(12).max();f['known_12lo']=f['low'].rolling(12).min()
        f['history']=np.arange(len(f));f['low5']=f['low'].rolling(5).min();f['high5']=f['high'].rolling(5).max()
        parts.append(f)
    return pd.concat(parts,ignore_index=True)


def plans(five):
    f=prepare(five);events=[]
    for r in f[f['history']>=250].to_dict('records'):
        a=r['atr'];dom=r['dominant'];w=r['known_width'];c=r['close'];vwap=r['vwap']
        proposals=[]
        if r['range_balanced'] and 4*a<=w<=10*a:
            for d in (1,-1):
                if dom==-d:continue
                entry=r['known_low']+.1*a if d==1 else r['known_high']-.1*a
                if d*(c-entry)>0 and abs(c-entry)<=.35*w:
                    proposals.append((0,d,'limit',entry,entry-d*2*a,(r['known_high']+r['known_low'])/2))
        if dom:
            d=int(dom);entry=r['slow'];extreme=r['low5']-.25*a if d==1 else r['high5']+.25*a
            stop=min(entry-2*a,extreme) if d==1 else max(entry+2*a,extreme)
            if d*(c-entry)>0:proposals.append((1,d,'limit',entry,stop,r['known_high'] if d==1 else r['known_low']))
            width=r['known_12hi']-r['known_12lo']
            if width<=3*a and r['relative_volume']>=1:
                entry=r['known_12hi']+.1*a if d==1 else r['known_12lo']-.1*a
                stop=r['known_12lo']-.25*a if d==1 else r['known_12hi']+.25*a
                stop=min(stop,entry-2*a) if d==1 else max(stop,entry+2*a)
                proposals.append((2,d,'stop_market',entry,stop,entry+d*width))
        for family,d,kind,entry,stop,target in proposals:
            gap=d*(entry-stop)
            if (gap<=0 or gap/entry>.006 or abs(entry-vwap)>2.5*a
                    or d*(target-entry)<=0 or unit_net(d,entry,target)<=0):continue
            events.append(dict(family=family,side=d,kind=kind,plan_at=int(r['start']+360),
                               level=entry,stop=stop,target=target,atr=a,
                               unit_net_risk=-unit_net(d,entry,stop),signal_bar=int(r['start'])))
    return sorted(events,key=lambda e:(e['plan_at'],e['family'],e['side']))


def funding_cost(ft,fr,starts,opens,entry_at,until,side,entry,exit_price):
    cost=0.
    for k in range(np.searchsorted(ft,entry_at,side='right'),np.searchsorted(ft,until,side='left')):
        i=int(np.searchsorted(starts,ft[k]));proxy=opens[i] if i<len(starts) and starts[i]==ft[k] else max(entry,exit_price)
        cost+=max(side*fr[k],0.)*proxy
    return cost


def fill_and_follow(p,m,ft,fr):
    starts,op,hi,lo,cl=m;d=p['side'];level=p['level'];stop=p['stop'];target=p['target'];atr=p['atr'];start=p['plan_at']
    index=int(np.searchsorted(starts,start));fill=None;expiry=start+900
    for i in range(index,min(index+15,len(starts))):
        if starts[i]!=start+(i-index)*60:return None,'pending_data_gap',int(starts[i])
        if starts[i]>=expiry:break
        trigger=(lo[i]<=level-.1*atr if d==1 else hi[i]>=level+.1*atr) if p['kind']=='limit' else (hi[i]>=level+.1*atr if d==1 else lo[i]<=level-.1*atr)
        if trigger:
            fill=i;break
        if d*((hi[i] if d==1 else lo[i])-target)>=0:return None,'target_before_fill',int(starts[i]+60)
    if fill is None:return None,'unfilled_expiry',expiry
    entry=level if p['kind']=='limit' else max(level,op[fill]) if d==1 else min(level,op[fill])
    t=int(starts[fill]);risk=p['unit_net_risk'];guard=None;best=entry;guard_bar=-1;mf=ma=0.;reason='censored_end';exit_price=None;exit_at=None
    if d*(entry-target)>=0:exit_price=entry;reason='gap_chase_exit';exit_at=t+60
    else:
        for j in range(fill,min(fill+120,len(starts))):
            if starts[j]!=t+(j-fill)*60:reason='censored_gap';exit_at=int(starts[j-1]+60);break
            active=(max(stop,guard) if d==1 else min(stop,guard)) if guard is not None else stop
            is_guard=guard is not None and d*(active-stop)>0
            if d*(op[j]-active)<=0:exit_price=op[j];reason='profit_guard_gap' if is_guard else 'gap_stop'
            else:
                sh=d*((lo[j] if d==1 else hi[j])-active)<=0
                th=j>fill and d*((hi[j] if d==1 else lo[j])-target)>=0
                if sh:exit_price=active;reason='profit_guard_exit' if is_guard else 'ambiguous_stop_first' if th else 'stop'
                elif th:exit_price=target;reason='target'
            if exit_price is not None:exit_at=int(starts[j]+60);break
            # Only closes after a possible fill are causal excursion evidence.
            quote=cl[j];mf=max(mf,d*(quote-entry));ma=max(ma,-d*(quote-entry))
            if d*(quote-best)>0:best=quote
            accrued=funding_cost(ft,fr,starts,op,t,int(starts[j]+60),d,entry,quote)
            effective_entry=entry*(1+d*SLIP)
            breakeven=(d*effective_entry+FEE*effective_entry+accrued)/((d-FEE)*(1-d*SLIP))
            threshold=max(abs(level-stop),2*d*(breakeven-entry))
            candidate=breakeven+.25*(best-breakeven);bar=int((starts[j]+60)//300)
            if (d*(best-entry)>=threshold and d*(quote-candidate)>=.5*atr
                    and (guard is None or (bar>guard_bar and d*(candidate-guard)>=max(.25*atr,entry*.0001)))):
                guard=candidate;guard_bar=bar  # effective in the NEXT minute
        if exit_price is None and reason=='censored_end' and min(fill+120,len(starts))-fill==120:
            exit_price=cl[fill+119];exit_at=int(starts[fill+119]+60);reason='time_exit'
    if exit_at is None:exit_at=int(starts[-1]+60)
    fund=funding_cost(ft,fr,starts,op,t,exit_at,d,entry,exit_price or entry)
    row=dict(p,entry_at=t,entry=entry,exit_at=exit_at,exit=exit_price,reason=reason,
             net_r=unit_net(d,entry,exit_price,fund)/risk if exit_price is not None else np.nan,
             stress_r=unit_net(d,entry,exit_price,fund,2.)/risk if exit_price is not None else np.nan,
             target_hit=reason=='target',early_stop_5m=reason in ('stop','gap_stop','ambiguous_stop_first') and exit_at-t<=300,
             funding_cost_unit=fund,mfe_close_price=mf,mae_close_price=ma,
             unexpected_gap_risk_cap_breach=d*(entry-stop)/entry>.006)
    return row,None,max(exit_at,t+2700)


def run(folder,output,integrity_path):
    folder=Path(folder);output=Path(output);output.mkdir(parents=True,exist_ok=True)
    sources=verify_manifests(folder);one=load_archives(folder,'1m');fund=load_archives(folder,'5m','fundingRate').sort_values('calc_time')
    audit=json.loads(Path(integrity_path).read_text());bad=audit.get('quarantined_starts',[int(r['start']) for r in audit['monthly_disagreements']])
    one=one[~(one['start']//300*300).isin(bad)].reset_index(drop=True);five=aggregate_minutes(one)
    m=tuple(one[k].to_numpy() for k in ('start','open','high','low','close'));ft=(fund['calc_time'].to_numpy()/1000).astype('int64');fr=fund['last_funding_rate'].to_numpy()
    events=plans(five);blocked={k:0 for k in range(3)};counts={k:{} for k in range(3)};rows=[];not_filled={}
    for p in events:
        family=p['family'];t=p['plan_at'];day=t//86400
        if t<blocked[family] or counts[family].get(day,0)>=8:continue
        row,reason,until=fill_and_follow(p,m,ft,fr);blocked[family]=until
        if row is not None:rows.append(row);counts[family][int(row['entry_at']//86400)]=counts[family].get(int(row['entry_at']//86400),0)+1
        else:not_filled[reason]=not_filled.get(reason,0)+1
    ledger=pd.DataFrame(rows);ledger.to_csv(output/'preplanned_trades.csv.gz',index=False,compression={'method':'gzip','mtime':0})
    summaries={};start_all=int(one['start'].iloc[0]);end_all=int(one['start'].iloc[-1]+60)
    for year in sorted(pd.to_datetime(ledger['entry_at'],unit='s',utc=True).dt.year.unique()):
        start=max(int(pd.Timestamp(f'{year}-01-01',tz='UTC').timestamp()),start_all);end=min(int(pd.Timestamp(f'{year+1}-01-01',tz='UTC').timestamp()),end_all)
        for family,name in enumerate(NAMES):
            selected=ledger[(ledger['family']==family)&(ledger['entry_at']>=start)&(ledger['entry_at']<end)].copy()
            selected.loc[selected['exit_at']>end,['net_r','stress_r']]=np.nan
            summary=score(selected,start,end);closed=selected[np.isfinite(selected['net_r'])]
            summary['net_win_fraction']=float((closed['net_r']>0).mean()) if len(closed) else None
            summary['profit_guard_exits']=int(closed['reason'].str.startswith('profit_guard').sum())
            summary['gap_risk_cap_breaches']=int(closed['unexpected_gap_risk_cap_breach'].sum())
            summaries[f'{year}:{name}']=summary
    result=dict(protocol_sha256=digest(Path(__file__).with_name('entry_location_protocol_20261020.json')),
                original_minute_rows=sum(r['csv_rows'] for r in sources if r['kind']=='klines' and r['interval']=='1m'),
                usable_minute_rows=len(one),quarantined_starts=bad,eligible_plans=len(events),unfilled=not_filled,
                summary=summaries,source_manifest=sources,trade_ledger_sha256=digest(output/'preplanned_trades.csv.gz'),
                venue='Binance USD-M BTCUSDT, not LBank',actual_pnl=False,production_changed=False,
                entry_fill_assumptions='Hypothetical conservative penetrated limits / adverse stop-market gaps; no queue/fill verification. Same entry-minute profit touches not credited.',
                native_execution_verified=False)
    (output/'preplanned_report.json').write_text(json.dumps(result,indent=2,allow_nan=False)+'\n')
    print(json.dumps({k:v for k,v in result.items() if k!='source_manifest'},indent=2,allow_nan=False),flush=True)


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--folder',required=True);p.add_argument('--output',required=True);p.add_argument('--integrity',required=True);a=p.parse_args();run(a.folder,a.output,a.integrity)
