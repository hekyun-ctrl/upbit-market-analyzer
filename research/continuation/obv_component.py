"""Published unfiltered OBV component, not full-paper or LBank certification."""
import argparse,hashlib,json
from pathlib import Path
import numpy as np
import pandas as pd
from research.long_history.evaluate import load_archives,verify_manifests,digest

SHORT=(2,6,12,18,24,30,48,96,144,168)
LONG=(2,6,12,18,24,30,48,96,144,168,192)
FREQ=(10,30,60)
FEE=.0006;SLIP=.0002

def rule_grid():return [(f,p,q) for f in FREQ for p in SHORT for q in LONG if p<q]

def rolling(x,n):
    y=np.full(len(x),np.nan)
    if len(x)>=n:
        c=np.r_[0.,np.cumsum(x,dtype=float)];y[n-1:]=(c[n:]-c[:-n])/n
    return y

def signals(close,volume,p,q):
    delta=np.r_[0.,np.diff(close)]
    obv=np.cumsum(np.where(delta>=0,volume,-volume))
    a=rolling(obv,p);b=rolling(obv,q)
    return (a>=b)&np.isfinite(b)

def prepare_frequency(one,frequency):
    records=[]
    for _,s in one.groupby((one.start.diff()!=60).cumsum()):
        bucket=s.start//(frequency*60)*(frequency*60)
        g=s.groupby(bucket).agg(first=('start','first'),last=('start','last'),count=('start','count'),close=('close','last'),volume=('volume','sum'))
        g=g[(g['count']==frequency)&(g['first']==g.index)&(g['last']==g.index+(frequency-1)*60)]
        available=g.index.to_numpy(dtype='int64')+frequency*60+60
        idx=np.searchsorted(available,s.start.to_numpy(),side='right')-1
        records.append((s.index.to_numpy(),idx,g.close.to_numpy(),g.volume.to_numpy()))
    return records


def minute_positions(one,frequency,p,q,cache=None):
    desired=np.zeros(len(one),dtype='int8')
    for places,idx,close,volume in (prepare_frequency(one,frequency) if cache is None else cache):
        if len(close)<q:continue
        want=signals(close,volume,p,q);good=idx>=0
        desired[places[good]]=want[idx[good]]
    return desired

def quality_months(one):
    t=one.start.to_numpy(dtype='int64');op=one.open.to_numpy();v=one.volume.to_numpy()
    if np.any(v<0) or np.any(op<=0) or not np.isfinite(op).all():raise ValueError('invalid original inputs')
    segment_start=np.maximum.accumulate(np.where(np.r_[True,np.diff(t)!=60],t,t[0]))
    # Full previous close and longest 192x60m context; do not use gap warmup PnL.
    ready=(t-segment_start)>=(193*3600+60)
    month=pd.to_datetime(t,unit='s',utc=True).strftime('%Y-%m')
    records=[];slices={}
    for label,indices in pd.Series(np.arange(len(t))).groupby(month):
        ix=indices.to_numpy();start=int(pd.Timestamp(label+'-01',tz='UTC').timestamp());end=int((pd.Timestamp(label+'-01',tz='UTC')+pd.offsets.MonthBegin(1)).timestamp())
        i=int(ix[0]);j=int(ix[-1]+1)
        valid=(len(ix)==(end-start)//60 and t[i]==start and t[j-1]+60==end and ready[ix].all() and j<len(t) and t[j]==end)
        records.append(dict(month=label,valid=bool(valid),minutes=len(ix),expected_minutes=(end-start)//60,history_ready=bool(ready[ix].all())))
        if valid:slices[label]=(i,j)
    return records,slices

def monthly_path(t,price,desired,funding,i,j):
    # The final array element is the next-month open, used only to close sleeves.
    pos=desired[i:j].astype(float);prev=np.r_[0.,pos[:-1]]
    gross=pos*np.log(price[i+1:j+1]/price[i:j])
    fund=prev*np.log1p(-np.maximum(funding[i:j],0.))
    fund[-1]+=pos[-1]*np.log1p(-max(funding[j],0.))
    enter=(pos>prev).astype(float);leave=(pos<prev).astype(float);leave[-1]+=pos[-1]
    base=gross+fund+enter*(np.log1p(-FEE)-np.log1p(SLIP))+leave*(np.log1p(-FEE)+np.log1p(-SLIP))
    stress=gross+fund+enter*(np.log1p(-2*FEE)-np.log1p(2*SLIP))+leave*(np.log1p(-2*FEE)+np.log1p(-2*SLIP))
    days=t[i:j]//86400*86400;unique,counts=np.unique(days,return_counts=True);first=np.r_[0,np.cumsum(counts)[:-1]]
    summary=np.stack([np.add.reduceat(x,first) for x in (gross,base,stress)],axis=1)
    if np.any(~np.isfinite(summary)):raise ValueError('nonfinite return')
    entries=np.flatnonzero(enter);trades=[]
    ends=np.flatnonzero((pos<prev));ends=np.r_[ends,len(pos)]
    for a in entries:
        b=int(ends[ends>a][0]);end_t=int(t[i+b]);entry_t=int(t[i+a]);rr=np.log(price[i+b]/price[i+a])
        fc=float(np.maximum(funding[i+a+1:i+b+1],0.).sum())
        funding_log=float(np.log1p(-np.maximum(funding[i+a+1:i+b+1],0.)).sum())
        net=rr+funding_log+2*np.log1p(-FEE)+np.log1p(-SLIP)-np.log1p(SLIP)
        net2=rr+funding_log+2*np.log1p(-2*FEE)+np.log1p(-2*SLIP)-np.log1p(2*SLIP)
        trades.append(dict(entry_at=entry_t,exit_at=end_t,entry_proxy=float(price[i+a]),exit_proxy=float(price[i+b]),holding_minutes=(end_t-entry_t)/60,
                           gross_log=float(rr),net_log=float(net),stress_log=float(net2),adverse_funding_rate_sum=fc))
    if abs(sum(x['net_log'] for x in trades)-float(base.sum()))>1e-8:raise ValueError('trade/daily accounting discrepancy')
    return unique,summary,trades

def select_rules(month_returns,labels,target,top):
    end=pd.Timestamp(target+'-01');begin=end-pd.DateOffset(months=12)
    train=[i for i,m in enumerate(labels) if begin<=pd.Timestamp(m+'-01')<end]
    if len(train)<9:return None,[labels[i] for i in train]
    score=month_returns[:,train].sum(axis=1)
    order=np.lexsort((np.arange(len(score)),-score))
    return order[:top], [labels[i] for i in train]

def portfolio_curve(daily,rule_ids):
    # Each sleeve receives equal initial cash, then grows without daily rebalancing.
    return np.exp(np.cumsum(daily[rule_ids],axis=1)).mean(axis=0)

def summarize(dates,logr,bootstrap_seed=20261020):
    if not len(logr):return dict(days=0)
    equity=np.r_[1.,np.exp(np.cumsum(logr))];dd=-(equity/np.maximum.accumulate(equity)-1).min()
    rng=np.random.default_rng(bootstrap_seed);n=len(logr);means=[]
    ends=np.empty(n,dtype=int);last=n
    for i in range(n-1,-1,-1):
        if i==n-1 or dates[i+1]-dates[i]!=86400:last=i+1
        ends[i]=min(i+7,last)
    # Blocks end at actual calendar gaps; absent months are never made adjacent.
    for _ in range(2000):
        pieces=[];count=0
        while count<n:
            k=int(rng.integers(0,n));piece=np.arange(k,ends[k]);pieces.append(piece);count+=len(piece)
        indices=np.concatenate(pieces)[:n]
        means.append(float(np.asarray(logr)[indices].mean()))
    years={}
    for year in sorted(set(pd.to_datetime(dates,unit='s',utc=True).year)):
        r=logr[pd.to_datetime(dates,unit='s',utc=True).year==year];v=np.r_[1.,np.exp(np.cumsum(r))]
        years[str(year)]=dict(observed_days=len(r),return_pct=float(np.expm1(r.sum())*100),observed_day_drawdown_pct=float(-(v/np.maximum.accumulate(v)-1).min()*100))
    return dict(days=n,return_pct=float((equity[-1]-1)*100),observed_day_drawdown_pct=float(dd*100),daily_mean_log_return=float(np.mean(logr)),
        daily_mean_7day_block_bootstrap95=[float(x) for x in np.quantile(means,[.025,.975])],years=years,
        calendar_continuity=False,bootstrap_blocks_cross_missing_days=False,scope='Concatenated complete-quality months only; excluded months have unknown strategy result, not zero return.')

def main(output):
    root=Path(__file__).resolve().parents[3];out=Path(output);out.mkdir(parents=True,exist_ok=True)
    folders=[root/'binance-futures-blind-2022',root/'binance-futures-blind-2023',root/'binance-futures-verified'];parts=[];funds=[];sources=[];original=0;removed=0
    for folder,audit in zip(folders,['integrity_final_2022.json','integrity_final_2023.json','integrity_final_2024_2026.json']):
        sources+=verify_manifests(folder);m=load_archives(folder,'1m');original+=len(m)
        bad=json.loads((Path(__file__).resolve().parents[1]/'long_history'/audit).read_text())['quarantined_starts'];keep=~(m.start//300*300).isin(bad)
        removed+=int((~keep).sum());parts.append(m[keep]);funds.append(load_archives(folder,'5m','fundingRate'))
    one=pd.concat(parts).sort_values('start').reset_index(drop=True);fund=pd.concat(funds).sort_values('calc_time')
    quality,slices=quality_months(one);labels=sorted(slices);t=one.start.to_numpy(dtype='int64');price=one.open.to_numpy();funding=np.zeros(len(one))
    ft=(fund.calc_time.to_numpy()/1000).astype('int64');idx=np.searchsorted(t,ft);good=(idx<len(t));good[good]&=t[idx[good]]==ft[good]
    funding[idx[good]]=fund.last_funding_rate.to_numpy()[good]
    cache={f:prepare_frequency(one,f) for f in FREQ}
    grids=rule_grid();month_returns=np.zeros((len(grids),len(labels)));all_daily={};all_trades=[];daily_rows=[]
    # Per-rule calculation is bounded; only daily paths and trade events persist.
    for ri,(f,p,q) in enumerate(grids):
        desired=minute_positions(one,f,p,q,cache[f])
        for mi,label in enumerate(labels):
            i,j=slices[label];dates,v,trades=monthly_path(t,price,desired,funding,i,j)
            month_returns[ri,mi]=v[:,1].sum()
            if label not in all_daily:all_daily[label]=dict(dates=dates,values=np.empty((len(grids),len(dates),3)))
            all_daily[label]['values'][ri]=v
            for row in trades:all_trades.append(dict(rule_id=ri,frequency_minutes=f,short=p,long=q,month=label,**row))
        if ri%25==0:print('computed',ri,'/',len(grids),flush=True)
    selections=[];portfolio_daily=[];chosen_sleeves=[];trade_frame=pd.DataFrame(all_trades)
    for label in labels:
        if not ('2024-01'<=label<='2026-09'):continue
        d=all_daily[label]
        for top in (1,50):
            ids,train=select_rules(month_returns,labels,label,top)
            if ids is None:continue
            selections.append(dict(month=label,portfolio=f'best{top}',training_months=train,rule_ids=[int(x) for x in ids],rules=[grids[x] for x in ids]))
            paths=np.stack([portfolio_curve(d['values'][:,:,kind],ids) for kind in range(3)],axis=1)
            returns=np.diff(np.log(np.vstack([np.ones(3),paths])),axis=0)
            for day,v in zip(d['dates'],returns):portfolio_daily.append(dict(day=int(day),month=label,portfolio=f'best{top}',gross_log=float(v[0]),net_log=float(v[1]),stress_log=float(v[2])))
            sub=trade_frame[(trade_frame.month==label)&trade_frame.rule_id.isin(ids)].copy();sub['portfolio']=f'best{top}';sub['initial_sleeve_weight']=1/len(ids);chosen_sleeves.append(sub)
    # Matched-calendar buy/hold comparison. Missing months stay missing.
    evaluated=sorted({x['month'] for x in portfolio_daily})
    for label in evaluated:
        i,j=slices[label];dates,v,trades=monthly_path(t,price,np.ones(len(one),dtype='int8'),funding,i,j)
        for day,row in zip(dates,v):portfolio_daily.append(dict(day=int(day),month=label,portfolio='buyhold_matched',gross_log=float(row[0]),net_log=float(row[1]),stress_log=float(row[2])))
        sub=pd.DataFrame(trades);sub['month']=label;sub['rule_id']=-1;sub['portfolio']='buyhold_matched';sub['initial_sleeve_weight']=1.;chosen_sleeves.append(sub)
    daily_frame=pd.DataFrame(portfolio_daily);selected_trades=pd.concat(chosen_sleeves,ignore_index=True) if chosen_sleeves else pd.DataFrame()
    evaluation_decisions=[]
    for row in quality:
        label=row['month']
        if not ('2024-01'<=label<='2026-09'):continue
        _,train=select_rules(month_returns,labels,label,1)
        evaluation_decisions.append(dict(month=label,included=label in evaluated,complete_training_months=len(train),reason='included' if label in evaluated else 'incomplete_data_or_context' if not row['valid'] else 'fewer_than9_complete_training_months'))
    summaries={}
    for name,g in daily_frame.groupby('portfolio'):
        g=g.sort_values('day')
        for field in ('gross_log','net_log','stress_log'):summaries[name+':'+field]=summarize(g.day.to_numpy(),g[field].to_numpy())
        trades=selected_trades[selected_trades.portfolio==name]
        for field in ('gross_log','net_log','stress_log'):
            capital=1.;positive=negative=0.
            for label,mt in trades.groupby('month',sort=True):
                pnl=[]
                for _,sleeve in mt.groupby('rule_id'):
                    amount=capital*float(sleeve.initial_sleeve_weight.iloc[0])
                    for r in sleeve.sort_values('entry_at').to_dict('records'):
                        change=amount*np.expm1(r[field]);pnl.append(change);amount+=change
                positive+=sum(x for x in pnl if x>0);negative-=sum(x for x in pnl if x<0);capital+=sum(pnl)
            if abs(capital-1-summaries[name+':'+field]['return_pct']/100)>1e-8:raise ValueError('portfolio closed-trade PnL mismatch')
            summaries[name+':'+field]['observed_subset_profit_factor']=float(positive/negative) if negative else None
        summaries[name+':trade_diagnostics']=dict(sleeved_closed_trades=len(trades),unique_observed_days=g.day.nunique(),win_fraction=float((trades.net_log>0).mean()),
            mean_hold_minutes=float(trades.holding_minutes.mean()),median_hold_minutes=float(trades.holding_minutes.median()),
            caution='Sleeves overlap in same BTC asset; these are not independent live alerts, and no source SL/TP1/urgent-warning contract exists.')
    daily_frame.to_csv(out/'portfolio_daily.csv.gz',index=False,compression={'method':'gzip','mtime':0})
    selected_trades.to_csv(out/'selected_sleeve_trades.csv.gz',index=False,compression={'method':'gzip','mtime':0})
    (out/'monthly_choices.json').write_text(json.dumps(selections,indent=2)+'\n')
    result=dict(candidate_id='deprez_frommel_2024_unfiltered_obv_intraday_component_v1',protocol_sha256=digest(Path(__file__).with_name('obv_component_protocol_20261020.json')),code_sha256=digest(__file__),source_pdf_sha256=digest(root/'published-paper-audit/deprez_frommel_2024.pdf'),
        original_minutes=original,quarantined_minutes=removed,usable_minutes=len(one),rule_count=len(grids),quality_months=quality,evaluation_months=sorted(daily_frame.month.unique()),evaluation_decisions=evaluation_decisions,summaries=summaries,
        source_manifests=sources,artifacts={p.name:digest(p) for p in out.glob('*') if p.name!='report.json'},production_changed=False,profitable_strategy_certified=False,full_paper_replication=False)
    (out/'report.json').write_text(json.dumps(result,indent=2,allow_nan=False)+'\n')
    print(json.dumps(dict(rule_count=len(grids),evaluation_months=result['evaluation_months'],summaries=summaries),indent=2),flush=True)

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--output',required=True);main(p.parse_args().output)
