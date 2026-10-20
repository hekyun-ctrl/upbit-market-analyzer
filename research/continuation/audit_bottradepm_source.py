"""Pinned source ledger/causality audit, NOT a profitability backtest.

Review-owned AST definitions only. No source package, downloader, optimizer,
institutional entrypoint, credential, account, order, or synthetic alpha run.
"""
from __future__ import annotations
import argparse, ast, hashlib, json, logging, math, subprocess, zipfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
import numpy as np
import pandas as pd

PIN='dcd1ac3e2a32204c2943bff000644b6f95486aa5'
DEFS={
 'src/indicators.py':['ema','atr','rsi','adx'],
 'src/strategy.py':['StrategyConfig','Position','MonthlyState','add_features','compute_position_size','is_breakout_entry','is_pullback_entry','is_breakdown_entry','is_short_pullback_entry'],
 'src/data_loader.py':['resample_ohlcv'],
 'src/performance.py':['max_drawdown_pct','average_monthly_return_pct','monthly_returns_stats','summarize_report'],
 'src/backtest.py':['_entry_fill','_exit_fill','run_backtest'],
}

def digest(path): return hashlib.sha256(Path(path).read_bytes()).hexdigest()
def clean(x):
    if isinstance(x,dict):return {str(k):clean(v) for k,v in x.items()}
    if isinstance(x,list):return [clean(v) for v in x]
    if isinstance(x,(float,np.floating)):return float(x) if np.isfinite(x) else None
    if isinstance(x,(np.integer,)):return int(x)
    if isinstance(x,(np.bool_,)):return bool(x)
    return x

def kernels(repo):
    repo=Path(repo)
    head=subprocess.check_output(['git','-C',str(repo),'rev-parse','HEAD'],text=True).strip()
    if head!=PIN:raise ValueError('wrong pinned source')
    ns=dict(np=np,pd=pd,dataclass=dataclass,Any=Any,Path=Path,json=json,math=math,__name__=__name__)
    definitions=[]
    for path,names in DEFS.items():
        tree=ast.parse((repo/path).read_text())
        nodes=[n for n in tree.body if isinstance(n,(ast.ClassDef,ast.FunctionDef)) and n.name in names]
        if {n.name for n in nodes}!=set(names):raise ValueError('missing definition')
        definitions += [dict(path=path,name=n.name,line=n.lineno,ast_sha256=hashlib.sha256(ast.dump(n).encode()).hexdigest()) for n in nodes]
        exec(compile(ast.Module(body=nodes,type_ignores=[]),str(repo/path),'exec'),ns)
    return ns,definitions

def fixtures(repo,out):
    out=Path(out);out.mkdir(parents=True,exist_ok=True)
    ns,defs=kernels(repo)
    source_fill=dict(short_entry=ns['_entry_fill'](100.,.0002),short_exit=ns['_exit_fill'](100.,.0002),
        adverse_short_entry=99.98,adverse_short_exit=100.02)
    # Fixed accounting fixture, not strategy signals or source market performance.
    df=pd.DataFrame(dict(open=[100.,101.,98.],high=[100.,103.,99.],low=[100.,99.,97.],
        close=[100.,101.,98.],volume=[1.,1.,1.],atr14=[1.,1.,1.],_enter=[True,False,False]),
        index=pd.date_range('2024-01-01',periods=3,freq='4h',tz='UTC'))
    probe=dict(ns)
    probe['resample_ohlcv']=lambda f,tf:f
    probe['add_features']=lambda a,b,c:a
    probe['is_breakout_entry']=lambda r,c:bool(r['_enter'])
    probe['is_pullback_entry']=lambda r,c:False
    probe['is_breakdown_entry']=lambda r,c:False
    probe['is_short_pullback_entry']=lambda r,c:False
    # Recompile only run_backtest so globals are the explicit fixture harness.
    tree=ast.parse((Path(repo)/'src/backtest.py').read_text())
    node=next(n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name=='run_backtest')
    exec(compile(ast.Module(body=[node],type_ignores=[]),'reviewed_source_fixture','exec'),probe)
    cfg=ns['StrategyConfig'](risk_pct=.00018,use_monthly_controls=False,move_stop_to_breakeven_after_tp1=False)
    report=probe['run_backtest'](df,config=cfg,starting_equity=10000.,output_dir=out/'synthetic_partial',data_source='synthetic_accounting_probe_NOT_market',save_plot=False)
    ledger=pd.read_csv(out/'synthetic_partial/trades.csv')
    gross=sum((float(r.price)-float(ledger.iloc[0].price))*float(r.qty) for r in ledger.itertuples() if r.event_type!='entry')
    actual_fees=sum(float(r.price)*float(r.qty)*cfg.fee_rate for r in ledger.itertuples())
    actual_net=gross-actual_fees
    entryfee=-float(ledger.iloc[0].realized_pnl)
    result=dict(short_slippage=source_fill,
        partial_event_statistics=dict(source_report=clean(report),events=clean(ledger.where(pd.notna(ledger),None).to_dict('records')),
            economic_closed_campaigns=1,economic_closed_winners=int(actual_net>0),
            actual_notional_cash_net=actual_net,source_cash_net=report['ending_equity']-10000.,
            source_minus_correct_cash=(report['ending_equity']-10000.)-actual_net,
            extra_entry_fee=entryfee,source_equity_terminal=float(pd.read_csv(out/'synthetic_partial/equity_curve.csv').iloc[-1].equity)),
        funding_interface_absent='funding' not in (Path(repo)/'src/backtest.py').read_text().lower())
    # Controlled unfinished campaign: source force-closes, not a censor.
    tail=probe['run_backtest'](df.iloc[:1],config=cfg,starting_equity=10000.,output_dir=out/'synthetic_tail',data_source='synthetic_tail_probe_NOT_market',save_plot=False)
    result['tail_forced_close']=clean(tail)
    result['tail_equity_terminal']=float(pd.read_csv(out/'synthetic_tail/equity_curve.csv').iloc[-1].equity)
    return result,defs

def load_minutes(folder,manifest):
    manifest=json.loads(Path(manifest).read_text())
    parts=[];sources=[]
    for month in ('2024-01','2024-02','2024-03'):
        m=next(x for x in manifest['archives'] if x['month']==month)
        path=Path(folder)/'klines'/'1m'/f'BTCUSDT-1m-{month}.zip'
        if digest(path)!=m['sha256']:raise ValueError('official manifest SHA mismatch')
        with zipfile.ZipFile(path) as z:
            name=z.namelist()[0];raw=z.read(name)
            f=pd.read_csv(path,header=0 if raw.startswith(b'open_time,') else None,names=['start','open','high','low','close','volume','close_time','quote','trades','buy_volume','buy_quote','ignore'])
        if len(f)!=m['csv_rows']:raise ValueError('row count mismatch')
        parts.append(f);sources.append(dict(month=month,url=m['url'],checksum_url=m['checksum_url'],
            zip_sha256=digest(path),csv_sha256=hashlib.sha256(raw).hexdigest(),rows=len(f)))
    m=pd.concat(parts,ignore_index=True)
    if not (m.start.diff().iloc[1:]==60000).all():raise ValueError('minute time gap')
    if m.start.duplicated().any() or m.isna().any().any():raise ValueError('duplicate or missing')
    if ((m.high<m[['open','close']].max(axis=1))|(m.low>m[['open','close']].min(axis=1))|(m.volume<0)).any():raise ValueError('invalid OHLCV')
    m.index=pd.to_datetime(m.start,unit='ms',utc=True)
    return m[['open','high','low','close','volume']],sources

def real_prefix(repo,folder,manifest,out):
    out=Path(out);ns,_=kernels(repo);m,sources=load_minutes(folder,manifest)
    hour=ns['resample_ohlcv'](m,'1h');four=ns['resample_ohlcv'](hour,'4h');day=ns['resample_ohlcv'](hour,'1d')
    cfg=ns['StrategyConfig']()
    full=ns['add_features'](four,day,cfg)
    # Dates fixed in protocol BEFORE inspecting feature output; all reused.
    cut=pd.Timestamp('2024-03-11T00:00:00Z')
    prefix=hour[hour.index<cut]
    past=ns['add_features'](ns['resample_ohlcv'](prefix,'4h'),ns['resample_ohlcv'](prefix,'1d'),cfg)
    paired=full.loc[past.index]
    changed=past['high_vol_regime']!=paired['high_vol_regime']
    control=(past['daily_regime']!=paired['daily_regime'])
    cols=['ema20','ema50','atr14','rsi14']
    causal_equal={c:bool(np.allclose(past[c],paired[c],equal_nan=True)) for c in cols}
    rows=[]
    for d in range(60,91):
        cutoff=hour.index[0]+pd.Timedelta(days=d,hours=4)
        h=hour[hour.index<cutoff]
        p=ns['add_features'](ns['resample_ohlcv'](h,'4h'),ns['resample_ohlcv'](h,'1d'),cfg)
        label=p.index[-1]
        rows.append(dict(label=str(label),decision_after_close=str(cutoff),
            source_prefix_d_adx=float(p.loc[label,'d_adx14']),full_day_d_adx=float(full.loc[label,'d_adx14']),
            source_prefix_d_ema200=float(p.loc[label,'d_ema200']),full_day_d_ema200=float(full.loc[label,'d_ema200']),
            prefix_bear=bool(p.loc[label,'daily_bear_regime']),full_day_bear=bool(full.loc[label,'daily_bear_regime']),
            prefix_lagged_bull=bool(p.loc[label,'daily_regime']),full_day_lagged_bull=bool(full.loc[label,'daily_regime'])))
    query=pd.DataFrame(rows);query.to_csv(out/'real_intraday_prefix.csv',index=False)
    comparison=pd.DataFrame({'label':past.index.astype(str),'past_high_vol':past['high_vol_regime'].to_numpy(),
        'full_high_vol':paired['high_vol_regime'].to_numpy(),'changed':changed.to_numpy()})
    comparison.to_csv(out/'real_fullsample_quantile_prefix.csv',index=False)
    outdata=dict(raw_minutes=len(m),complete_hours=len(hour),complete_4h=len(four),complete_days=len(day),
        sources=sources,manifest_sha256=digest(manifest),
        fullsample_quantile=dict(past_bars=len(past),cutoff=str(cut),past_flags_changed=int(changed.sum()),
            prefix_quantile=float(past.volatility_20.quantile(.6)),full_quantile=float(full.volatility_20.quantile(.6)),
            lagged_daily_bull_changes=int(control.sum()),causal_indicator_equal=causal_equal),
        intraday_prefix=dict(queries=len(query),d_adx_changed=int((query.source_prefix_d_adx!=query.full_day_d_adx).sum()),
            d_ema200_changed=int((query.source_prefix_d_ema200!=query.full_day_d_ema200).sum()),
            bear_flags_changed=int((query.prefix_bear!=query.full_day_bear).sum()),
            lagged_bull_flags_changed=int((query.prefix_lagged_bull!=query.full_day_lagged_bull).sum())),
        market_backtest_performed=False,headline_profit_impact_measured=False,
        sample='Already-observed Binance USD-M2024Q1,not originalauthorinput/spot/Delta/nativeLBank. Featurecausality only.',
        fixed_query_files_sha256={p.name:digest(p) for p in (out/'real_intraday_prefix.csv',out/'real_fullsample_quantile_prefix.csv')})
    return outdata

def inventory(repo):
    tree=[]
    for line in subprocess.check_output(['git','-C',str(repo),'ls-tree','-r','HEAD'],text=True).splitlines():
        meta,name=line.split('\t',1);tree.append(dict(path=name,git_blob_sha=meta.split()[2],sha256=digest(Path(repo)/name)))
    return tree

def main(a):
    out=Path(a.output);out.mkdir(parents=True,exist_ok=True)
    start=datetime.now(timezone.utc).isoformat()
    f,defs=fixtures(a.repo,out);real=real_prefix(a.repo,a.folder,a.manifest,out)
    report=dict(version='bottradepm_contract_and_realprefix_v1',started_utc=start,completed_utc=datetime.now(timezone.utc).isoformat(),
        source_commit=PIN,source_url='https://github.com/pymammadov/bottradepm/tree/'+PIN,
        source_tree=inventory(a.repo),reviewed_definitions=defs,script_sha256=digest(__file__),
        protocol_sha256=digest(Path(__file__).with_name('ledger_source_protocol_20261020_1413.json')),
        synthetic_contract=f,real_data=real,
        original_closed_ledger_recovered=False,original_input_checksum_recovered=False,
        author_best_oos=json.loads((Path(a.repo)/'outputs/report.json').read_text())['best_oos'],
        author_stress=json.loads((Path(a.repo)/'outputs/stress_test_summary.json').read_text())['stress_results'],
        net_expectancy_95_lower=None,fullcost_double_profit=None,profit_factor=None,drawdown_R=None,
        tp1_wilson_lower=None,urgent_warning_rate=None,actual_signal_frequency=None,native_fills_verified=False,
        strategy_profitability_backtest_performed=False,replacement_certified=False,forward_started=False,production_changed=False,
        decision='exclude_current_public_release_missing_BTCledger_fullcost_and_causal_contract;not_allregime_methods',
        limitations=['No attribution of author publishedoutcomes tosynthetic orrealinput established.',
            'Partialcash fixtures use forcedentry/harness features,not marketstrategy.',
            'Sourcefunding/interface missing andper-sidecosts flawed;no fullcost historicalprofit ornative certification.',
            'Featurefuturedependence doesnotestablish everytradedecision/totalheadlineimpact.',
            'Laggedbull control andrecursivepriceindicators retained evenwhen otherfeaturesfail.',
            'No repair,optimization,risk/leverage expansion,credentialuse,or operationalreplacement.'])
    (out/'report.json').write_text(json.dumps(clean(report),indent=2,allow_nan=False)+'\n')
    for name in ('report.json','stress_test_summary.json','best_model_report.json'):
        (out/('author_'+name+'.txt')).write_bytes((Path(a.repo)/'outputs'/name).read_bytes())
    print(json.dumps(clean(dict(real=real,synthetic=f,definitions=len(defs))),indent=2,allow_nan=False))

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--repo',required=True);p.add_argument('--folder',required=True)
    p.add_argument('--manifest',required=True);p.add_argument('--output',required=True);main(p.parse_args())
