"""Independent external-perpetual evaluation, not venue-transfer certification.

Frozen protocol: 2024 fit, 2025 validation, 2026 Jan-Sep final test. Original
minute executions, causally completed 5m decisions, conservative intrabar order,
actual historical funding rates with conservative price/cashflow assumptions.
Never changes production or exchange orders.
"""
import argparse,hashlib,io,json,math,os,zipfile
from pathlib import Path
from datetime import datetime,timezone
import numpy as np
import pandas as pd
from sklearn.ensemble import HistGradientBoostingRegressor
# Copied without numerical changes from the preregistered research module so
# this evidence package needs no production-monitor imports or credentials.
NAMES=('hour_trend_vwap_reclaim','range_failed_break','volume_compression_break')


def wilson_lower(wins,n):
    if not n:return None
    z=1.96;p=wins/n
    return (p+z*z/(2*n)-z*math.sqrt(p*(1-p)/n+z*z/(4*n*n)))/(1+z*z/n)

FEE=.0006;SLIP=.0002
COLUMNS=['start','open','high','low','close','volume','close_time','quote_volume','trades','buy_volume','buy_quote_volume','ignore']
FEATURES=['side','atr_pct','range_atr','relative_volume','body_atr','close_location','vwap_distance_atr','ret_1','ret_3','ret_12','ret_36','hour_dir','target_net_r','stop_pct','entry_deviation_atr','hour_sin','hour_cos','family_0','family_1','family_2']


def digest(path):return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def verify_manifests(folder):
    entries=[]
    for interval in ('1m','5m'):
        manifest=json.loads((Path(folder)/f'manifest-{interval}.json').read_text())
        for r in manifest['archives']:
            path=Path(folder)/r['kind']/r['interval']/Path(r['path']).name
            if digest(path)!=r['sha256']:raise ValueError('archive checksum changed')
            entries.append({k:v for k,v in r.items() if k not in ('first','last','path')})
    return entries


def load_archives(folder,interval,kind='klines'):
    paths=sorted((Path(folder)/kind/interval).glob('*.zip'));frames=[]
    for path in paths:
        with zipfile.ZipFile(path) as z:
            raw=z.read(z.namelist()[0]);header=not raw[:1].isdigit()
            frame=pd.read_csv(io.BytesIO(raw),header=0 if header else None)
            if kind=='klines':frame.columns=COLUMNS
            frames.append(frame)
    if not frames:raise ValueError('missing verified archives')
    df=pd.concat(frames,ignore_index=True)
    if kind=='klines':
        df=df.astype({k:'float64' for k in COLUMNS});df['start']=(df['start']/1000).astype('int64')
        df=df.sort_values('start').reset_index(drop=True)
        p=df[['open','high','low','close']].to_numpy()
        if (df['start'].duplicated().any() or not np.isfinite(df.to_numpy()).all() or (p<=0).any()
            or (df['volume']<0).any() or (df['high']<df[['open','close','low']].max(axis=1)).any()
            or (df['low']>df[['open','close','high']].min(axis=1)).any()):raise ValueError('invalid OHLCV')
        seconds=60 if interval=='1m' else 300
        if (df['start']%seconds).any():raise ValueError('unaligned archive')
    return df


def aggregate_minutes(minutes):
    m=minutes.copy();m['bucket']=m['start']//300*300
    g=m.groupby('bucket',sort=True)
    out=g.agg(start=('start','first'),open=('open','first'),high=('high','max'),low=('low','min'),close=('close','last'),volume=('volume','sum'),count=('start','count'),last=('start','last'))
    valid=(out['count']==5)&(out['start']==out.index)&(out['last']==out.index+240)
    return out[valid].drop(columns=['count','last']).reset_index(drop=True)


def wilder_atr(df):
    tr=pd.concat([df['high']-df['low'],(df['high']-df['close'].shift()).abs(),(df['low']-df['close'].shift()).abs()],axis=1).max(axis=1).to_numpy()
    out=np.full(len(df),np.nan)
    if len(tr)>14:
        value=tr[1:15].mean();out[14]=value
        for i in range(15,len(tr)):value=(13*value+tr[i])/14;out[i]=value
    return out


def feature_frame(segment):
    f=segment.copy();c=f['close'];a=wilder_atr(f);f['atr']=a
    typ=(f['high']+f['low']+c)/3
    f['vwap']=(typ*f['volume']).shift().rolling(20).sum()/f['volume'].shift().rolling(20).sum()
    f['relative_volume']=f['volume']/f['volume'].shift().rolling(20).median()
    f['prior_high']=f['high'].shift().rolling(20).max();f['prior_low']=f['low'].shift().rolling(20).min()
    f['box_high']=f['high'].shift().rolling(12).max();f['box_low']=f['low'].shift().rolling(12).min()
    f['width']=f['box_high']-f['box_low'];f['balanced']=(c.shift()-c.shift(12)).abs()<=.35*f['width']
    f['body_atr']=(c-f['open'])/a;f['atr_pct']=a/c
    f['range_atr']=(f['high']-f['low'])/a
    f['close_location']=(c-f['low'])/(f['high']-f['low']).replace(0,np.nan)
    f['vwap_distance_atr']=(c-f['vwap'])/a
    for n in (1,3,12,36):f[f'ret_{n}']=c.pct_change(n)
    # Label each complete UTC hour by its END; reindex never exposes a forming hour.
    hour=(f['start']//3600*3600).astype('int64')
    h=f.groupby(hour).agg(count=('start','count'),close=('close','last'),first=('start','first'),last=('start','last'))
    good=(h['count']==12)&(h['first']==h.index)&(h['last']==h.index+3300)
    hc=h['close'].where(good);delta=hc.diff();hd=pd.Series(np.where((delta>0)&(delta.shift()>0),1,np.where((delta<0)&(delta.shift()<0),-1,0)),index=h.index+3600)
    f['hour_dir']=hd.reindex(f['start']+300,method='ffill').to_numpy()
    return f


def build_events(five):
    parts=[];group=(five['start'].diff()!=300).cumsum()
    for _,segment in five.groupby(group):
        f=feature_frame(segment.reset_index(drop=True));f['history']=np.arange(len(f))
        a=f['atr'];d=f['hour_dir'];last=f['close'];o=f['open'];v=f['vwap'];w=f['width']
        records=[]
        for side in (1,-1):
            touch=(f['low']<=v)&(f['high']>=v)
            trend=(d==side)&(f['relative_volume']>=1)&touch&(side*(last-v)>0)&(side*(last-o)>0)
            level=f['box_low'] if side==1 else f['box_high'];extreme=f['low'] if side==1 else f['high']
            failed=f['balanced']&(side*(extreme-level)<-.1*a)&(side*(last-level)>0)&(side*(last-o)>0)
            breaklevel=f['box_high'] if side==1 else f['box_low']
            compress=(w<=3*a)&(f['relative_volume']>=1.5)&(side*(last-breaklevel)>.1*a)
            for family,mask in enumerate((trend,failed,compress)):
                target=(f['prior_high'] if side==1 else f['prior_low']) if family==0 else (f['box_high']+f['box_low'])/2 if family==1 else breaklevel+side*w
                selected=f.loc[mask&(f['history']>=250)].copy()
                selected['side']=side;selected['family']=family;selected['target']=target.loc[selected.index]
                selected['anchor']=extreme.loc[selected.index]-side*.25*a.loc[selected.index]
                # One-second publication lag -> first strictly later minute open.
                selected['entry_at']=selected['start']+360
                records.append(selected)
        parts.extend(records)
    return pd.concat(parts,ignore_index=True).sort_values(['entry_at','family','side']).reset_index(drop=True)


def unit_net(d,entry,exit_price,funding=0.,stress=1.):
    e=entry*(1+d*SLIP*stress);x=exit_price*(1-d*SLIP*stress)
    return d*(x-e)-FEE*stress*(e+x)-funding


def evaluate_events(events,minutes,funding):
    starts=minutes['start'].to_numpy(dtype='int64');op=minutes['open'].to_numpy();hi=minutes['high'].to_numpy();lo=minutes['low'].to_numpy();cl=minutes['close'].to_numpy()
    ft=(funding['calc_time'].to_numpy()/1000).astype('int64');fr=funding['last_funding_rate'].to_numpy()
    records=[];reject={}
    for e in events.to_dict('records'):
        d=int(e['side']);t=int(e['entry_at']);i=int(np.searchsorted(starts,t));why=None
        if i>=len(starts) or starts[i]!=t:why='entry_minute_missing'
        else:
            entry=op[i];atr=e['atr'];gap=d*(entry-e['anchor']);target=e['target']
            if gap<=0:why='entry_invalidated'
            else:
                gap=max(gap,1.5*atr,entry*.0005);stop=entry-d*gap
                if gap/entry>.006:why='stop_cap'
                elif abs(entry-e['vwap'])>2.5*atr:why='entry_extended'
                elif d*(target-entry)<=0:why='target_passed'
                elif unit_net(d,entry,target)<=0:why='no_net_target_room'
        if why:reject[why]=reject.get(why,0)+1;continue
        risk=-unit_net(d,entry,stop);end=min(i+120,len(starts));reason='censored_end';exit_price=None;exit_at=None;mf=ma=0.
        for j in range(i,end):
            if starts[j]!=t+(j-i)*60:reason='censored_gap';exit_at=int(starts[j-1]+60);break
            if d*(op[j]-stop)<=0:exit_price=op[j];reason='gap_stop'
            elif d*(op[j]-target)>=0:exit_price=target;reason='target'
            else:
                sh=d*((lo[j] if d==1 else hi[j])-stop)<=0;th=d*((hi[j] if d==1 else lo[j])-target)>=0
                if sh:exit_price=stop;reason='ambiguous_stop_first' if th else 'stop'
                elif th:exit_price=target;reason='target'
                else:
                    mf=max(mf,d*((hi[j] if d==1 else lo[j])-entry));ma=max(ma,-d*((lo[j] if d==1 else hi[j])-entry));continue
            exit_at=int(starts[j]+60);break
        if exit_price is None and reason=='censored_end' and end-i==120:exit_price=cl[end-1];exit_at=int(starts[end-1]+60);reason='time_exit'
        elif exit_at is None:exit_at=int(starts[end-1]+60)
        fund_cost=0.
        # Use archived rates. Favorable receipts are conservatively ignored;
        # use trade price at settlement as a proxy because mark price is absent.
        if exit_price is not None:
            k0=np.searchsorted(ft,t,side='right');k1=np.searchsorted(ft,exit_at,side='left')
            for k in range(k0,k1):
                mi=int(np.searchsorted(starts,ft[k]));mark_proxy=op[mi] if mi<len(starts) and starts[mi]==ft[k] else max(entry,exit_price)
                fund_cost+=max(d*fr[k],0.)*mark_proxy
        row=dict(e,entry=entry,stop=stop,unit_net_risk=risk,target_net_r=unit_net(d,entry,target)/risk,
                 stop_pct=gap/entry,entry_deviation_atr=d*(entry-e['close'])/atr,
                 hour_sin=math.sin(t%86400/86400*2*math.pi),hour_cos=math.cos(t%86400/86400*2*math.pi),
                 exit_at=exit_at,exit=exit_price,reason=reason,funding_cost_unit=fund_cost,
                 net_r=unit_net(d,entry,exit_price,fund_cost)/risk if exit_price is not None else np.nan,
                 stress_r=unit_net(d,entry,exit_price,fund_cost,2.)/risk if exit_price is not None else np.nan,
                 target_hit=reason=='target',early_stop_5m=reason in ('stop','gap_stop','ambiguous_stop_first') and exit_at-t<=300,
                 mfe_lower_price=mf,mae_lower_price=ma)
        for family in range(3):row[f'family_{family}']=float(e['family']==family)
        records.append(row)
    return pd.DataFrame(records),reject


def select_portfolio(rows,prediction=None,threshold=None):
    """One position, 45m between entries, eight/day, opposite ties abstain."""
    rows=rows.sort_values(['entry_at','family','side']);blocked=0;counts={};selected=[]
    for t,g in rows.groupby('entry_at',sort=True):
        if t<blocked:continue
        if prediction is not None:
            g=g.loc[prediction.loc[g.index]>threshold]
            if len(g):g=g.loc[[prediction.loc[g.index].idxmax()]] if g['side'].nunique()==1 else g.iloc[:0]
        elif g['side'].nunique()!=1:continue
        if not len(g):continue
        day=int(t//86400)
        if counts.get(day,0)>=8:continue
        r=g.iloc[0];selected.append(r.name);counts[day]=counts.get(day,0)+1;blocked=max(r['exit_at'],t+2700)
    return rows.loc[selected].copy()


def block_ci(values,seed=20261020):
    values=np.asarray(values,dtype=float);n=len(values)
    if n<14:return dict(lower95=None,upper95=None,lower99=None)
    rng=np.random.default_rng(seed);reps=[];length=7
    for _ in range(2000):
        origins=rng.integers(0,n,size=math.ceil(n/length));idx=((origins[:,None]+np.arange(length))%n).ravel()[:n]
        reps.append(values[idx].mean())
    return dict(lower95=float(np.quantile(reps,.025)),upper95=float(np.quantile(reps,.975)),lower99=float(np.quantile(reps,.005)))


def score(rows,start,end):
    closed=rows[np.isfinite(rows['net_r'])];values=closed['net_r'].to_numpy();stress=closed['stress_r'].to_numpy()
    n=len(closed);gains=values[values>0].sum();loss=-values[values<0].sum();cum=np.r_[0,np.cumsum(values)];dd=float(np.max(np.maximum.accumulate(cum)-cum))
    days=np.arange(start//86400,end//86400,dtype='int64');daily=closed.assign(day=(closed['entry_at']//86400).astype('int64')).groupby('day')['net_r'].sum().reindex(days,fill_value=0.)
    target=int(closed['target_hit'].sum());early=int(closed['early_stop_5m'].sum())
    return dict(trades=len(rows),closed=n,censored=len(rows)-n,target_count=target,target_fraction=target/n if n else None,
                target_wilson_lower=wilson_lower(target,n),net_total_r=float(values.sum()),net_expectancy_r=float(values.mean()) if n else None,
                profit_factor=float(gains/loss) if loss else None,stress_2x_total_r=float(stress.sum()),stress_2x_mean_r=float(stress.mean()) if n else None,
                max_drawdown_r=dd,early_stop_5m_fraction=early/n if n else None,
                signals_per_day=len(rows)/len(days),active_day_fraction=rows['entry_at'].floordiv(86400).nunique()/len(days),
                daily_net_r_ci=block_ci(daily.to_numpy()),actual_pnl=False,venue='Binance USD-M, not LBank')


def train_meta(rows):
    out={};predictions=pd.Series(np.nan,index=rows.index)
    for year in (2025,2026):
        cutoff=int(pd.Timestamp(f'{year}-01-01',tz='UTC').timestamp());end=int(pd.Timestamp(f'{year+1}-01-01',tz='UTC').timestamp())
        fit=rows[(rows['entry_at']<cutoff-86400)&(rows['exit_at']<cutoff-86400)&np.isfinite(rows['net_r'])]
        test=rows[(rows['entry_at']>=cutoff)&(rows['entry_at']<end)]
        fit=fit[np.isfinite(fit[FEATURES]).all(axis=1)];test=test[np.isfinite(test[FEATURES]).all(axis=1)]
        if len(fit)<300:raise ValueError('insufficient fixed-model training cases')
        model=HistGradientBoostingRegressor(max_iter=150,learning_rate=.05,max_leaf_nodes=7,min_samples_leaf=100,l2_regularization=1.,early_stopping=False,random_state=20261020)
        model.fit(fit[FEATURES],fit['net_r']);predictions.loc[test.index]=model.predict(test[FEATURES])
        out[str(year)]=dict(training_cases=len(fit),training_latest_exit=int(fit['exit_at'].max()),prediction_cases=len(test),
                            minimum_prediction=float(predictions.loc[test.index].min()),maximum_prediction=float(predictions.loc[test.index].max()))
    return predictions,out


def hourly_walkforward(minutes,five,funding):
    f=five.copy();f['bucket']=f['start']//3600*3600
    h=f.groupby('bucket').agg(open=('open','first'),high=('high','max'),low=('low','min'),close=('close','last'),count=('start','count'),first=('start','first'),last=('start','last'))
    h=h[(h['count']==12)&(h['first']==h.index)&(h['last']==h.index+3300)]
    execution=h.index.to_numpy()+3660;quotes=minutes.set_index('start')['open'].reindex(execution)
    if quotes.isna().any():
        valid=quotes.notna().to_numpy();h=h.loc[valid];execution=execution[valid];quotes=quotes[valid]
    if (np.diff(execution)!=3600).any():
        return dict(unavailable='hourly execution gap; no gap interpolation')
    price=quotes.to_numpy();returns=price[1:]/price[:-1]-1
    close=pd.Series(h['close'].to_numpy());signals=[];names=[]
    for fast in (5,7,10,15,20,30):
        for slow in (40,50,100,150,200):
            signals.append(np.where(close.ewm(span=fast,adjust=False).mean()>=close.ewm(span=slow,adjust=False).mean(),1.,-1.));names.append([fast,slow])
    s=np.asarray(signals);cost=(FEE+SLIP)*np.abs(np.diff(s,axis=1,prepend=np.zeros((len(s),1))))
    ft=funding['calc_time'].to_numpy()/1000;fr=funding['last_funding_rate'].to_numpy();fund=np.zeros(len(execution))
    for t,r in zip(ft,fr):
        i=np.searchsorted(execution,t,side='left')-1
        if 0<=i<len(fund):fund[i]+=r
    base=s[:,:-1]*returns-cost[:,:-1]-np.maximum(s[:,:-1]*fund[:-1],0.)
    chosen=np.zeros(len(h));steps=[];start=337;step=240
    while start<len(h)-1:
        train=base[:,start-337:start-1];std=train.std(axis=1,ddof=1);sharpe=np.divide(train.mean(axis=1),std,out=np.full(len(s),-np.inf),where=std>0)
        best=int(np.argmax(sharpe));end=min(start+step,len(h));chosen[start:end]=s[best,start:end]
        steps.append(dict(first_exec=int(execution[start]),last_training_exec=int(execution[start-1]),parameters=names[best],training_bars=336))
        start=end
    real_cost=(FEE+SLIP)*np.abs(np.diff(chosen,prepend=0));net=chosen[:-1]*returns-real_cost[:-1]-np.maximum(chosen[:-1]*fund[:-1],0.)
    stress=chosen[:-1]*returns-2*real_cost[:-1]-np.maximum(chosen[:-1]*fund[:-1],0.)
    output={}
    for year in (2025,2026):
        st=int(pd.Timestamp(f'{year}-01-01',tz='UTC').timestamp());en=min(int(pd.Timestamp(f'{year+1}-01-01',tz='UTC').timestamp()),int(execution[-1]))
        mask=(execution[:-1]>=st)&(execution[:-1]<en);r=net[mask];rr=stress[mask];times=execution[:-1][mask]
        eq=np.cumprod(1+r);dd=1-eq/np.maximum.accumulate(np.r_[1.,eq])[1:]
        daily=pd.Series(r,index=pd.to_datetime(times,unit='s',utc=True)).resample('1D').sum()
        output[str(year)]=dict(first_execution_utc=datetime.fromtimestamp(int(times[0]),timezone.utc).isoformat(),last_execution_utc=datetime.fromtimestamp(int(times[-1]),timezone.utc).isoformat(),hourly_observations=len(r),net_return_unlevered=float(np.prod(1+r)-1),stress_return_unlevered=float(np.prod(1+rr)-1),
            buy_hold_return=float(price[np.flatnonzero(mask)[-1]+1]/price[np.flatnonzero(mask)[0]]-1),
            annualized_sharpe=float(r.mean()/r.std(ddof=1)*math.sqrt(24*365)),max_drawdown_fraction=float(dd.max()),
            position_change_units=float(np.abs(np.diff(chosen,prepend=0))[:-1][mask].sum()),daily_return_ci=block_ci(daily.to_numpy()),
            interpretation='Separate always-long/short hourly direction research; no TP1/stop contract, not approved for leveraged live entry')
    return dict(summary=output,walkforward_steps=steps)


def main(folder,output):
    output=Path(output);output.mkdir(parents=True,exist_ok=True)
    manifest=verify_manifests(folder)
    (output/'verified_sources.json').write_text(json.dumps(manifest,indent=2)+'\n')
    one=load_archives(folder,'1m');original=load_archives(folder,'5m');funding=load_archives(folder,'5m','fundingRate').sort_values('calc_time').reset_index(drop=True)
    source_minute_count=len(one)
    integrity=json.loads(Path(__file__).with_name('integrity_final_2024_2026.json').read_text())
    bad=integrity['quarantined_starts']
    one=one[~(one['start']//300*300).isin(bad)].reset_index(drop=True)
    five=aggregate_minutes(one);joined=five.merge(original,on='start',suffixes=('_aggregate','_archive'))
    differences={k:int((np.abs(joined[k+'_aggregate']-joined[k+'_archive'])>max(1e-6,1e-8)).sum()) for k in ('open','high','low','close','volume')}
    quality=dict(source_minute_rows=source_minute_count,quarantined_intervals=bad,quarantined_minute_count=source_minute_count-len(one),minute_rows=len(one),five_rows=len(five),original_five_rows=len(original),funding_rows=len(funding),
                 minute_gaps=int((one['start'].diff().iloc[1:]!=60).sum()),five_gaps=int((five['start'].diff().iloc[1:]!=300).sum()),aggregate_archive_differences=differences)
    print('DATA_QUALITY',json.dumps(quality),flush=True)
    if any(differences.values()):raise ValueError('minute aggregation differs from official 5m archive; investigate before performance results')
    events=build_events(five);print('PATTERNS',len(events),flush=True)
    labelled,rejections=evaluate_events(events,one,funding);labelled.to_csv(output/'labelled_development_and_test.csv.gz',index=False,compression={'method':'gzip','mtime':0})
    print('LABELLED',len(labelled),flush=True)
    predictions,fit_info=train_meta(labelled);labelled['prediction']=predictions
    summaries={};ledgers=[]
    for year in (2025,2026):
        start=int(pd.Timestamp(f'{year}-01-01',tz='UTC').timestamp());end=min(int(pd.Timestamp(f'{year+1}-01-01',tz='UTC').timestamp()),int(five['start'].iloc[-1]+300))
        part=labelled[(labelled['entry_at']>=start)&(labelled['entry_at']<end)].copy()
        boundary=part['exit_at']>end
        part.loc[boundary,['net_r','stress_r']]=np.nan
        part.loc[boundary,'reason']='censored_split_boundary'
        for name,subset in [(NAMES[i],part[part['family']==i]) for i in range(3)]+[('all_unfiltered',part),('fixed_meta_net_r',part)]:
            selected=select_portfolio(subset,predictions,.10) if name=='fixed_meta_net_r' else select_portfolio(subset)
            summaries[f'{year}:{name}']=score(selected,start,end)
            selected=selected.copy();selected['evaluation']=f'{year}:{name}';ledgers.append(selected)
    ledger=pd.concat(ledgers,ignore_index=True);ledger.to_csv(output/'selected_trades.csv.gz',index=False,compression={'method':'gzip','mtime':0})
    hourly=hourly_walkforward(one[one['start']>max(bad)+300].copy(),five[five['start']>max(bad)+300].copy(),funding)
    report=dict(protocol_sha256=digest(Path(__file__).with_name('protocol_20261020.json')),data_quality=quality,
        raw_patterns=len(events),modelled_candidates=len(labelled),rejections=rejections,meta_fit=fit_info,intraday=summaries,hourly_ema=hourly,
        execution='First original minute open after 5m-close + 1s publication assumption. Minute conflict stop first; zero quote liquidity or exact fills claimed.',
        funding='Archived settlement rates; adverse payments at trade-price proxy, favorable receipts ignored. Not actual account funding or mark-price verification.',
        final_test_hyperparameter_changes=0,production_changed=False,lbank_profitability_validated=False,
        output_files={name:dict(sha256=digest(output/name),bytes=(output/name).stat().st_size) for name in ('selected_trades.csv.gz','labelled_development_and_test.csv.gz')})
    (output/'report.json').write_text(json.dumps(report,indent=2,allow_nan=False)+'\n')
    print(json.dumps(report,indent=2,allow_nan=False),flush=True)


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--folder',required=True);p.add_argument('--output',required=True);a=p.parse_args();main(a.folder,a.output)
