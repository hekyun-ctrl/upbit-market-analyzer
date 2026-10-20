"""Frozen public-rule transfer screen, NOT exact RLXBT or native LBank replication.

No fitting, parameter search, orders, daemon, or production imports. Original
spot-volume threshold is retained numerically on futures BTC volume; venue and
volume distributions differ. All historical periods were previously observed.
"""
import argparse
import hashlib
import io
import json
import math
from datetime import datetime, timezone
from pathlib import Path
import zipfile
import numpy as np
import pandas as pd

VERSION='rlxbt_pinbar_transfer_v1'
SL=.0035
TP=.007
HOLD=25
COLS=['start','open','high','low','close','volume','close_time','quote_volume','trades','buy_volume','buy_quote_volume','ignore']

def sha(path):return hashlib.sha256(Path(path).read_bytes()).hexdigest()

def signals(a):
    width=a[:,2]-a[:,3]
    valid=(width>0)&(a[:,4]>0)&(a[:,5]>35)&(width/a[:,4]>.003)
    side=np.zeros(len(a),dtype=int)
    side[valid&((a[:,4]-a[:,3])>width*.85)]=1
    side[valid&((a[:,2]-a[:,4])>width*.85)]=-1
    return side

def load(folder,manifest_path,kind,interval):
    manifest=json.loads(Path(manifest_path).read_text());frames=[];sources=[]
    for item in sorted(manifest['archives'],key=lambda x:x['month']):
        if item['kind']!=kind:continue
        path=folder/kind/interval/Path(item['url']).name
        if sha(path)!=item['sha256']:raise ValueError('archive SHA mismatch: '+str(path))
        with zipfile.ZipFile(path) as z:
            if len(z.namelist())!=1:raise ValueError('unexpected archive members')
            raw=z.read(z.namelist()[0])
        frame=pd.read_csv(io.BytesIO(raw),header=0 if not raw[:1].isdigit() else None)
        if kind=='klines':frame.columns=COLS
        frames.append(frame)
        sources.append({**{k:v for k,v in item.items() if k not in ('path','first','last')},
                        'csv_sha256':hashlib.sha256(raw).hexdigest()})
    if not frames:raise ValueError('no source archives')
    return pd.concat(frames,ignore_index=True),sources

def follow(a,valid,entry,side,funding):
    e=float(a[entry,1]);stop=e*(1-side*SL);target=e*(1+side*TP)
    adverse=0.;favorable=0.;events=[]
    for j in range(entry,min(entry+HOLD,len(a))):
        if not valid[j] or (j>entry and a[j,0]!=a[j-1,0]+60):
            return dict(status='unknown_gap',entry_index=entry,entry_at=int(a[entry,0]),side=int(side),entry_reference=e)
        op,hi,lo,cl=map(float,a[j,1:5])
        if j-entry<5:
            adverse=max(adverse,(e-lo)/e if side==1 else (hi-e)/e)
            favorable=max(favorable,(hi-e)/e if side==1 else (e-lo)/e)
        stop_hit=lo<=stop if side==1 else hi>=stop
        target_hit=hi>=target if side==1 else lo<=target
        gap_stop=op<=stop if side==1 else op>=stop
        if gap_stop:price,reason=op,'stop_open_gap'
        elif stop_hit:price,reason=stop,'stop'  # both touched => stop first
        elif target_hit:price,reason=target,'target'
        elif j==entry+HOLD-1:price,reason=cl,'time'
        else:continue
        until=int(a[j,0])+60
        for t,rate in funding:
            if a[entry,0]<t<=until:
                # Mark not available: use latest observed minute open, never a future close.
                k=min(j,np.searchsorted(a[:,0],t,side='right')-1)
                events.append(dict(time=float(t),rate=float(rate),price_proxy=float(a[k,1]),
                                   debit=float(max(side*rate,0)*a[k,1])))
        return dict(status='closed',entry_index=entry,entry_at=int(a[entry,0]),exit_at=until,
            exit_index=j,side=int(side),entry_reference=e,exit_reference=float(price),reason=reason,
            same_bar_both_touched=bool(stop_hit and target_hit),funding_events=events,
            funding_debit_unit=sum(x['debit'] for x in events),
            first5_whole_bar_adverse_fraction=float(adverse),
            first5_whole_bar_favorable_fraction=float(favorable),
            stopped_within5minute_bar_bound=bool(reason.startswith('stop') and j-entry<5))
    return dict(status='unknown_end',entry_index=entry,entry_at=int(a[entry,0]),side=int(side),entry_reference=e)

def cash(row,multiple):
    d=row['side'];e=row['entry_reference'];x=row['exit_reference']
    ef=e*(1+d*.0002*multiple);xf=x*(1-d*.0002*multiple)
    gross=d*(x-e);slipped=d*(xf-ef);fee=.0006*multiple*(ef+xf)
    funding=row['funding_debit_unit']*multiple
    return dict(gross_R=gross/(e*SL),net_R=(slipped-fee-funding)/(e*SL),
                entry_fill=ef,exit_fill=xf,commission_unit=fee,funding_unit=funding)

def replay(a,valid,funding,delay_minutes=0):
    s=signals(a);rows=[];busy=-1;skipped_invalid=0
    for i in np.flatnonzero(s):
        if not valid[i]:skipped_invalid+=1;continue
        if int(a[i,0])+60<=busy:continue
        entry=int(i)+1+delay_minutes
        if entry>=len(a):continue
        if int(a[entry,0])<=busy:continue
        if a[entry,0]!=a[i,0]+60*(1+delay_minutes) or not valid[i:entry+1].all():
            skipped_invalid+=1;continue
        row=follow(a,valid,entry,int(s[i]),funding)
        row.update(signal_at=int(a[i,0])+60,delay_minutes=delay_minutes)
        if row['status']=='closed':
            row['base']=cash(row,1);row['double']=cash(row,2)
            busy=row['exit_at']-1
        else:busy=int(a[entry,0])+HOLD*60-1  # no fictional gap liquidation or immediate overlap
        rows.append(row)
    return rows,dict(raw_signal_count=int(np.count_nonzero(s)),skipped_invalid=skipped_invalid)

def wilson(w,n):
    if n==0:return None
    p=w/n;z=1.96
    return (p+z*z/(2*n)-z*math.sqrt(p*(1-p)/n+z*z/(4*n*n)))/(1+z*z/n)

def metrics(rows,first,last):
    closed=[x for x in rows if x['status']=='closed'];days=(last-first)//86400+1
    if not closed:return dict(closed=0,unknown=len(rows),calendar_days=days)
    r=np.array([x['base']['net_R'] for x in closed]);double=np.array([x['double']['net_R'] for x in closed])
    gross=np.array([x['base']['gross_R'] for x in closed]);equity=np.r_[0,np.cumsum(r)]
    positive=float(r[r>0].sum());negative=float(-r[r<0].sum())
    daily_r=np.zeros(days);daily_n=np.zeros(days)
    for x in closed:
        k=(x['entry_at']-first)//86400;daily_r[k]+=x['base']['net_R'];daily_n[k]+=1
    lower={};rng=np.random.default_rng(20261020)
    for block in (7,30):
        samples=[]
        for _ in range(2000):
            offsets=rng.integers(0,days,size=math.ceil(days/block))
            ids=((offsets[:,None]+np.arange(block))%days).ravel()[:days]
            n=daily_n[ids].sum()
            if n:samples.append(float(daily_r[ids].sum()/n))
        lower[str(block)]=float(np.percentile(samples,2.5)) if samples else None
    targets=sum(x['reason']=='target' for x in closed)
    return dict(closed=len(closed),unknown=len(rows)-len(closed),calendar_days=days,
        gross_sum_R=float(gross.sum()),base_sum_R=float(r.sum()),double_sum_R=float(double.sum()),
        base_mean_R=float(r.mean()),net_expectancy_block95_lower_R=lower,
        gross_profit_R=positive,gross_loss_R=negative,PF=positive/negative if negative else None,
        observed_closed_max_drawdown_R=float(np.max(np.maximum.accumulate(equity)-equity)),
        target_first_count=targets,target_first_rate=targets/len(closed),tp1_wilson_lower=wilson(targets,len(closed)),
        first5_stop_bar_bound_rate=sum(x['stopped_within5minute_bar_bound'] for x in closed)/len(closed),
        actual_urgent_warning_rate=None,alarms_per_calendar_day=len(rows)/days,
        opportunity_day_fraction=len({x['entry_at']//86400 for x in rows})/days,
        median_first5_whole_bar_adverse_fraction=float(np.median([x['first5_whole_bar_adverse_fraction'] for x in closed])),
        funding_debit_unit_sum=float(sum(x['funding_debit_unit'] for x in closed)),
        both_touched_stop_first_count=sum(x['same_bar_both_touched'] for x in closed),
        complete_calendar_account_return=False)

def main():
    p=argparse.ArgumentParser();p.add_argument('--folder',type=Path,required=True)
    p.add_argument('--output-dir',type=Path,required=True);args=p.parse_args()
    if args.output_dir.exists():raise FileExistsError(args.output_dir)
    start=datetime.now(timezone.utc).isoformat();root=Path(__file__).resolve().parents[2]
    minute,sources=load(args.folder,root/'research/long_history/results/source_manifest_2024_2026_1m.json','klines','1m')
    fund,fs=load(args.folder,args.folder/'manifest-5m.json','fundingRate','5m');sources+=fs
    a=minute[COLS[:6]].to_numpy(dtype=float);a[:,0]/=1000
    if not np.isfinite(a).all() or (a[:,1:5]<=0).any() or (a[:,5]<0).any():raise ValueError('nonfinite or invalid OHLCV')
    if (np.diff(a[:,0])<=0).any() or (a[:,0]%60).any():raise ValueError('duplicate, unordered or unaligned minute')
    if (a[:,2]<a[:,[1,3,4]].max(axis=1)).any() or (a[:,3]>a[:,[1,2,4]].min(axis=1)).any():raise ValueError('bad OHLC')
    if int(a[0,0])!=1704067200 or int(a[-1,0])!=1790812740:raise ValueError('frozen calendar bounds changed')
    audit_path=root/'research/long_history/integrity_final_2024_2026.json'
    bad=json.loads(audit_path.read_text())['quarantined_starts'];valid=~np.isin(a[:,0]//300*300,bad)
    funding=np.c_[fund['calc_time'].to_numpy()/1000,fund['last_funding_rate'].to_numpy()]
    if not np.isfinite(funding).all() or (np.diff(funding[:,0])<=0).any():raise ValueError('bad funding records')
    if funding[0,0]>a[0,0]+1 or funding[-1,0]<a[-1,0]-28800 or (np.diff(funding[:,0])>28801).any():raise ValueError('funding coverage gap; do not replace with zero')
    first=int(a[0,0])//86400*86400;last=int(a[-1,0])//86400*86400
    report=dict(version=VERSION,started_utc=start,implementation_sha256=sha(__file__),
        protocol_sha256=sha(Path(__file__).with_name('pinbar_transfer_protocol_20261020.json')),
        source_original_spot=True,calculation_venue='Binance USD-M BTCUSDT perpetual',native_LBank=False,
        reused_all_periods=True,optimization_performed=False,full_source_replication=False,
        source_rows=len(a),missing_minutes=int(((a[-1,0]-a[0,0])//60+1)-len(a)),
        quarantined_minutes=int((~valid).sum()),quarantined_starts=bad,quality_policy_sha256=sha(audit_path),
        funding_rows=len(funding),scenarios={},actual_forward_started=False,eligible=False,production_changed=False)
    args.output_dir.mkdir(parents=True)
    for delay in (0,1):
        rows,counts=replay(a,valid,funding,delay);key=f'delay_{delay}m'
        ledger=args.output_dir/(key+'_ledger.jsonl')
        ledger.write_text(''.join(json.dumps(r,separators=(',',':'))+'\n' for r in rows))
        summary=metrics(rows,first,last);years={}
        for year in (2024,2025,2026):
            begin=int(datetime(year,1,1,tzinfo=timezone.utc).timestamp());end=min(last,int(datetime(year+1,1,1,tzinfo=timezone.utc).timestamp())-86400)
            subset=[x for x in rows if begin<=x['entry_at']<=end+86399]
            years[str(year)]=metrics(subset,begin,end)
        report['scenarios'][key]=dict(counts=counts,all=summary,years=years,ledger_sha256=sha(ledger),ledger_file=ledger.name)
    report['completed_utc']=datetime.now(timezone.utc).isoformat()
    (args.output_dir/'report.json').write_text(json.dumps(report,indent=2)+'\n')
    (args.output_dir/'sources.json').write_text(json.dumps(sources,indent=2)+'\n')
    print(json.dumps(report,indent=2))

if __name__=='__main__':main()
