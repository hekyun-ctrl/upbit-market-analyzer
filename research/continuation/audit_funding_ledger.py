"""Original fixed IS/OOS public-ledger arithmetic; not an alpha backtest."""
from __future__ import annotations
import argparse,ast,__future__,hashlib,json,math,subprocess
from datetime import datetime,timezone
from pathlib import Path
from types import SimpleNamespace
import numpy as np
import pandas as pd

PIN='776003eb85f3c1e6e92683ee39d6ebf7cade78a0'
def sha(b):return hashlib.sha256(b).hexdigest()
def clean(x):
    if isinstance(x,dict):return {str(k):clean(v) for k,v in x.items()}
    if isinstance(x,(list,tuple)):return [clean(v) for v in x]
    if isinstance(x,(np.integer,)):return int(x)
    if isinstance(x,(np.bool_,)):return bool(x)
    if isinstance(x,(float,np.floating)):return float(x) if np.isfinite(x) else None
    if isinstance(x,(pd.Timestamp,datetime)):return x.isoformat()
    return x
def source(repo,path):return subprocess.check_output(['git','-C',str(repo),'show',f'{PIN}:{path}'])
def kernel(repo,path,names):
    raw=source(repo,path);tree=ast.parse(raw);nodes=[n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name in names]
    if {n.name for n in nodes}!=set(names):raise ValueError('missing reviewed definition')
    ns=dict(pd=pd,np=np,hashlib=hashlib,json=json)
    exec(compile(ast.Module(body=nodes,type_ignores=[]),path,'exec',flags=__future__.annotations.compiler_flag),ns)
    return ns,[dict(path=path,name=n.name,line=n.lineno,ast_sha256=sha(ast.dump(n).encode())) for n in nodes]
def metric(rows,key):
    r=rows[rows.known_outcome].sort_values('exit_time');v=r[key].to_numpy(float)
    curve=np.r_[0.,np.cumsum(v)]
    return dict(closed=len(v),sum_USD=float(v.sum()),mean_USD=float(v.mean()) if len(v) else None,
        PF=float(v[v>0].sum()/-v[v<0].sum()) if (v<0).any() else None,
        net_wins=int((v>0).sum()),max_closed_drawdown_USD=float((np.maximum.accumulate(curve)-curve).max()))
def block_ci(daily,key,block):
    rng=np.random.default_rng(20261020);n=len(daily);b=min(block,n)
    starts=rng.integers(0,n,size=(5000,math.ceil(n/b)))
    ix=((starts[:,:,None]+np.arange(b))%n).reshape(5000,-1)[:,:n]
    count=daily['count'].to_numpy()[ix].sum(axis=1);sums=daily[key].to_numpy()[ix].sum(axis=1)
    valid=count>0;v=sums[valid]/count[valid]
    return dict(block_days=block,valid_samples=int(valid.sum()),lo_USD=float(np.quantile(v,.025)),hi_USD=float(np.quantile(v,.975)))
def data_inventory(repo):
    reg,defs=kernel(repo,'python/src/registry.py',['_md5','data_hash'])
    rows=[];frames={}
    for suffix in ['', '_2yr','_full']:
        p=f'data/raw/btcusdt_1h{suffix}.csv';f=pd.read_csv(Path(repo)/p);f.index=pd.to_datetime(f.open_time_ms,unit='ms',utc=True)
        frames[suffix]=f
        v=f[['open','high','low','close','volume','turnover']].to_numpy(float)
        cleanpath=f'data/clean/btcusdt_1h{suffix}.parquet';c=pd.read_parquet(Path(repo)/cleanpath)
        common=f.index.intersection(c.index)
        priceerr=float(np.max(np.abs(f.loc[common,['open','high','low','close']].to_numpy()-c.loc[common,['open','high','low','close']].to_numpy())))
        h=pd.read_csv(Path(repo)/f'data/raw/btcusdt_funding{suffix}.csv')
        rows.append(dict(raw=p,clean=cleanpath,rows=len(f),first=f.index[0],last=f.index[-1],duplicates=int(f.index.duplicated().sum()),
            gap_steps=int((f.index.to_series().diff().dropna()!=pd.Timedelta(hours=1)).sum()),finite=bool(np.isfinite(v).all()),positive_prices=bool((v[:,:4]>0).all()),
            bad_OHLC=int(((f.high<f[['open','low','close']].max(axis=1))|(f.low>f[['open','high','close']].min(axis=1))).sum()),
            clean_rows=len(c),clean_common_prices=len(common),maximum_raw_clean_price_error=priceerr,
            funding_rows=len(h),funding_duplicates=int(h.timestamp_ms.duplicated().sum()),clean_registry_prefix_MD5=reg['data_hash'](c),
            registry_hash_warning='Original hash covers first100index/closeandlen,notwholedata;ourfullSHA256isrecorded.'))
    return rows,frames,defs
def period(repo,name,raw,rawpath,out,extract):
    root=Path(repo)/'results'/name;s=json.loads((root/'summary.json').read_text())
    events=pd.read_parquet(root/'trades.parquet').copy();events['timestamp']=pd.to_datetime(events.timestamp,utc=True)
    eq=pd.read_parquet(root/'equity.parquet').iloc[:,0];pos=pd.read_parquet(root/'position.parquet').iloc[:,0];pnl=pd.read_parquet(root/'pnl.parquet')
    if not eq.index.equals(pos.index) or not eq.index.equals(pnl.index) or events.timestamp.diff().dropna().lt(pd.Timedelta(0)).any():raise ValueError('artifact index mismatch')
    if len(events)%2:raise ValueError('unpaired order row')
    if not np.isfinite(events[['qty','price','notional_usd','fee_usd','slippage_usd']].to_numpy()).all():raise ValueError('nonfinite events')
    funded=pnl.funding_pnl.copy();cumfund=0.;cash=10000.;stresscash=10000.;position=0.;orders=[];campaigns=[]
    for i,e in enumerate(events.itertuples()):
        sign=1 if e.side=='buy' else -1;qty=float(e.qty);forced=bool(i==len(events)-1 and e.timestamp==eq.index[-1] and abs(pos.iloc[-1])>1e-9)
        rawprice=e.price if forced else e.price/(1+sign*.0001)
        twiceprice=rawprice if forced else rawprice*(1+sign*.0002)
        twicefee=abs(qty*twiceprice)*.0012
        cash-=sign*qty*e.price+e.fee_usd;stresscash-=sign*qty*twiceprice+twicefee;position+=sign*qty
        orders.append(dict(event=i,timestamp=e.timestamp,side=e.side,qty=qty,price=e.price,raw_price=rawprice,fee=e.fee_usd,reported_slippage=e.slippage_usd,
            double_price=twiceprice,double_fee=twicefee,trade_cash=cash,trade_cash_double=stresscash,position=position,source_forced_end=forced))
        if i%2==0:
            entry=e;entryorder=orders[-1]
        else:
            direction=1 if entry.side=='buy' else -1
            if e.side==entry.side or not np.isclose(e.qty,entry.qty,rtol=0,atol=1e-10) or abs(position)>1e-9:raise ValueError('partial/reversal unsupported;stop')
            f=funded[(funded.index>=entry.timestamp)&(funded.index<e.timestamp)]
            fund=float(f.sum());fd=float(f.where(f>=0,2*f).sum());cumfund+=fund
            gross=direction*entry.qty*(e.price-entry.price);net=gross-entry.fee_usd-e.fee_usd+fund
            gd=direction*entry.qty*(twiceprice-entryorder['double_price']);nd=gd-entryorder['double_fee']-twicefee+fd
            expected=pd.date_range(entry.timestamp,e.timestamp,freq='1h')
            complete=bool(expected.isin(raw.index).all())
            campaigns.append(dict(campaign=i//2,entry_time=entry.timestamp,exit_time=e.timestamp,direction=direction,qty=entry.qty,entry_price=entry.price,exit_price=e.price,
                raw_entry=entryorder['raw_price'],raw_exit=rawprice,gross_with_embedded_slip_USD=gross,fee_USD=entry.fee_usd+e.fee_usd,
                embedded_slippage_USD=direction*entry.qty*((rawprice-entryorder['raw_price'])-(e.price-entry.price)),reported_slippage_USD=entry.slippage_usd+e.slippage_usd,
                funding_USD=fund,funding_double_cost_USD=fd,net_USD=net,double_cost_net_USD=nd,source_forced_end=forced,raw_contiguous=complete,known_outcome=complete and not forced,
                initial_R=None,TP1_first=None,urgent_5min=None))
    o=pd.DataFrame(orders);c=pd.DataFrame(campaigns)
    for tcol in ['entry_time','exit_time']:c[tcol]=pd.to_datetime(c[tcol],utc=True)
    o.to_csv(out/f'{name}_orders_cash.csv',index=False);c.to_csv(out/f'{name}_campaigns.csv',index=False)
    sourcecash=10000+float(funded.sum())+float((-np.where(events.side=='buy',1.,-1.)*events.qty*events.price-events.fee_usd).sum())
    stress=stresscash+float(funded.where(funded>=0,2*funded).sum())
    rawprices=raw.reindex(pd.DatetimeIndex(events.timestamp))
    sourceclose=raw.reindex(eq.index).close
    if sourceclose.isna().any():raise ValueError('no complete raw price coverage')
    # Reproduce the source's accounting index: normal order effects booked one bar before timestamp.
    delta=np.where(o.side=='buy',1.,-1.)*o.qty.to_numpy()
    booked=pd.DatetimeIndex([row.timestamp if row.source_forced_end else row.timestamp-pd.Timedelta(hours=1) for row in o.itertuples()])
    cf=pd.Series(-delta*o.price.to_numpy()-o.fee.to_numpy(),index=booked).groupby(level=0).sum().reindex(eq.index,fill_value=0)
    shiftedpos=pd.Series(delta,index=booked).groupby(level=0).sum().reindex(eq.index,fill_value=0).cumsum()
    source_model_equity=10000+cf.cumsum()+funded.cumsum()+shiftedpos*sourceclose
    # Economic timestamp path uses the very same fills at their actual recorded timestamps.
    econcf=pd.Series(-delta*o.price.to_numpy()-o.fee.to_numpy(),index=pd.DatetimeIndex(o.timestamp)).groupby(level=0).sum().reindex(eq.index,fill_value=0)
    econpos=pd.Series(delta,index=pd.DatetimeIndex(o.timestamp)).groupby(level=0).sum().reindex(eq.index,fill_value=0).cumsum()
    economic_equity=10000+econcf.cumsum()+funded.cumsum()+econpos*sourceclose
    years=[]
    for year,yr in c.groupby(c.entry_time.dt.year):years.append(dict(year=int(year),base=metric(yr,'net_USD'),double=metric(yr,'double_cost_net_USD')))
    dates=pd.date_range(eq.index[0].normalize(),eq.index[-1].normalize(),freq='1D')
    known=c[c.known_outcome].copy();known['date']=known.entry_time.dt.normalize()
    daily=known.groupby('date').agg(net_USD=('net_USD','sum'),double_cost_net_USD=('double_cost_net_USD','sum'),count=('campaign','count')).reindex(dates,fill_value=0)
    daily.to_csv(out/f'{name}_daily_known_campaigns.csv',index_label='date')
    authored=extract(SimpleNamespace(trades=events));source_trade_USD=authored.to_numpy()* (c.qty*c.entry_price).to_numpy()
    c['source_extract_net_USD']=source_trade_USD;c['source_extract_minus_cash_USD']=c.source_extract_net_USD-c.net_USD
    c.to_csv(out/f'{name}_campaigns.csv',index=False)
    feeperiod=pnl.fees.to_numpy();bookfees=pd.Series(o.fee.to_numpy(),index=booked).groupby(level=0).sum().reindex(eq.index,fill_value=0).to_numpy()
    return dict(name=name,source_summary=s,bar_rows=len(eq),first=eq.index[0],last=eq.index[-1],source_order_events=len(events),economic_closed_campaigns=len(c),known_closed_campaigns=int(c.known_outcome.sum()),
        forced_end_campaigns=int(c.source_forced_end.sum()),raw_gap_campaigns=int((~c.raw_contiguous).sum()),calendar_days=len(dates),full_days_by_hours=(eq.index[-1]-eq.index[0]).total_seconds()/86400,
        entry_days=int(c.entry_time.dt.normalize().nunique()),entries_per_calendar_day=len(c)/len(dates),opportunity_day_fraction=c.entry_time.dt.normalize().nunique()/len(dates),
        known_base=metric(c,'net_USD'),known_double_cost=metric(c,'double_cost_net_USD'),yearly=years,
        block_uncertainty_USD={key:[block_ci(daily,key,b) for b in [7,30]] for key in ['net_USD','double_cost_net_USD']},
        source_published_terminal_change_USD=float(eq.iloc[-1]-10000),cash_replayed_terminal_change_USD=sourcecash-10000,conditional_double_cost_terminal_change_USD=stress-10000,
        source_funding_USD=float(funded.sum()),unallocated_funding_USD=float(funded.sum())-cumfund,source_pnl_decomposition_sum_USD=float((pnl.gross_pnl-pnl.fees-pnl.slippage+pnl.funding_pnl).sum()),
        source_fee_sum_USD=float(events.fee_usd.sum()),source_slippage_column_sum_USD=float(events.slippage_usd.sum()),
        maximum_fee_model_abs_error=float(abs(events.fee_usd-events.qty*events.price*.0006).max()),maximum_fee_booking_abs_error=float(np.max(abs(feeperiod-bookfees))),
        maximum_source_equity_replay_abs_error=float(abs(source_model_equity-eq).max()),source_vs_economic_timestamp_equity_max_abs_difference=float(abs(economic_equity-eq).max()),
        source_drawdown_pct=float(((eq/eq.cummax())-1).min()*100),economic_recorded_timestamp_drawdown_pct=float(((economic_equity/economic_equity.cummax())-1).min()*100),
        nextopen_exec_price_raw_open_max_abs_error=float(abs(o.raw_price.to_numpy()-np.where(o.source_forced_end,rawprices.close,rawprices.open)).max()),
        ordinary_events_booked_one_hour_early=int((~o.source_forced_end).sum()),source_extract_trade_net_sum_USD=float(c.source_extract_net_USD.sum()),
        source_extract_minus_correct_cash_sum_USD=float(c.source_extract_minus_cash_USD.sum()),source_extract_expected_difference_USD=float((-c.reported_slippage_USD-c.funding_USD).sum()),
        raw_price_path=rawpath)
def input_manifest(repo):
    paths=['python/src/backtester.py','python/src/strategies/funding_arb.py','python/src/data_loader.py','python/src/quant_stats.py','python/src/registry.py','results/registry.parquet']
    paths += [f'data/{kind}/btcusdt_{stem}{suffix}.{ext}' for suffix in ['', '_2yr','_full'] for kind,stem,ext in [('raw','1h','csv'),('raw','funding','csv'),('clean','1h','parquet')]]
    paths += [f'results/{n}/{p}' for n in ['train_default','test_default'] for p in ['summary.json','trades.parquet','equity.parquet','pnl.parquet','position.parquet']]
    inputs=[]
    for p in paths:
        b=source(repo,p);inputs.append(dict(path=p,sha256=sha(b),bytes=len(b),git_blob_sha=hashlib.sha1(('blob '+str(len(b))+'\0').encode()+b).hexdigest()))
        local=repo/p
        if local.exists() and local.read_bytes()!=b:raise ValueError('checkout source bytes differ')
    return inputs
def main(a):
    repo,out=Path(a.repo),Path(a.output);out.mkdir(parents=True,exist_ok=True);start=datetime.now(timezone.utc).isoformat()
    if subprocess.check_output(['git','-C',str(repo),'rev-parse','HEAD'],text=True).strip()!=PIN:raise ValueError('pin mismatch')
    inputs=input_manifest(repo)
    inventory,frames,defs=data_inventory(repo)
    extract,more=kernel(repo,'python/src/quant_stats.py',['extract_trade_returns']);defs+=more
    registry=pd.read_parquet(repo/'results/registry.parquet')
    # Root/default run is indexed by output_dir=results; match its original prefix hash.
    rootreg=registry[registry.output_dir=='results'].iloc[0]
    candidates=[x for x in inventory if x['clean_registry_prefix_MD5']==rootreg.data_hash]
    if len(candidates)!=1:raise ValueError('ambiguous root input hash')
    selected=candidates[0];suffix=selected['raw'].removeprefix('data/raw/btcusdt_1h').removesuffix('.csv');raw=frames[suffix]
    stats=[period(repo,n,raw,selected['raw'],out,extract['extract_trade_returns']) for n in ['train_default','test_default']]
    report=dict(version='funding_harvester_776003e_published_default_ledger_v1',source_commit=PIN,started_utc=start,completed_utc=datetime.now(timezone.utc).isoformat(),
        library_versions=dict(numpy=np.__version__,pandas=pd.__version__),
        source_inputs=inputs,reviewed_executed_pure_definitions=defs,data_quality=inventory,selected_root_run_input=selected,
        registry_rows=registry[['git_sha','git_dirty','data_hash','config_hash','output_dir','n_trades','oos_start']].to_dict('records'),
        registry_full_SHA256=False,source_venue='AuthorlabelBybit;originalversionedfilesonly_notindependentlyexchangeverified',periods=stats,
        script_sha256=sha(Path(__file__).read_bytes()),protocol_sha256=sha(Path(__file__).with_name('funding_ledger_protocol_20261020.json').read_bytes()),
        new_market_backtest_performed=False,published_ledger_cash_reconstructed=True,forward_started=False,eligible=False,production_changed=False,
        R_expectancy_95_lower=None,drawdown_R=None,TP1_Wilson_lower=None,urgent_5min_rate=None,native_manual_fill=None,
        decision='not_promoted;originaldefaultonly_conditionalledger_math_notnativeprofit_noTP1_intrabarprotect_forwardgate')
    (out/'report.json').write_text(json.dumps(clean(report),indent=2,allow_nan=False)+'\n')
    print(json.dumps(clean(stats),indent=2,allow_nan=False))

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--repo',required=True);p.add_argument('--output',required=True);main(p.parse_args())
