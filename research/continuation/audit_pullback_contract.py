"""Pinned pure-kernel + actual in-memory broker contract audit. NOT alpha BT."""
from __future__ import annotations
import argparse,ast,__future__,hashlib,io,json,logging,math,subprocess,tempfile,zipfile
from dataclasses import dataclass
from datetime import datetime,timezone
from pathlib import Path
from types import SimpleNamespace
import numpy as np
import pandas as pd
import yaml
import backtrader as bt

PIN='22271525f93d04188c2791d48e1029a6101d39c3'
DEFS={
 'src/trend_pullback/indicators.py':['ema','_rma','rsi','atr'],
 'src/trend_pullback/signal_engine.py':['compute_signals','_true_range','_rma','_dmi_adx','_compute_pb_count','_apply_block_repeats'],
 'src/trend_pullback/risk.py':['LevelResult','calc_long_levels','calc_short_levels','validate_risk'],
 'src/trend_pullback/strategy.py':['TrendPullbackStrategy'],
 'src/trend_pullback/datafeed.py':['load_ohlcv','_find_datetime_column','_validate_ohlcv'],
 'src/trend_pullback/reporting.py':['compute_summary','_compute_max_drawdown'],
 'src/trend_pullback/analyzers.py':['TradeListAnalyzer','EquityCurveAnalyzer'],
}
def sha(b):return hashlib.sha256(b).hexdigest()
def clean(x):
    if isinstance(x,dict):return {str(k):clean(v) for k,v in x.items()}
    if isinstance(x,(list,tuple)):return [clean(v) for v in x]
    if isinstance(x,(float,np.floating)):return float(x) if np.isfinite(x) else None
    if isinstance(x,(np.integer,)):return int(x)
    if isinstance(x,np.bool_):return bool(x)
    if isinstance(x,(pd.Timestamp,datetime)):return x.isoformat()
    return x
def source(repo,path):return subprocess.check_output(['git','-C',str(repo),'show',f'{PIN}:{path}'])
def kernels(repo):
    if subprocess.check_output(['git','-C',str(repo),'rev-parse','HEAD'],text=True).strip()!=PIN:raise ValueError('pin')
    ns=dict(np=np,pd=pd,bt=bt,math=math,Path=Path,dataclass=dataclass,StrategyParams=SimpleNamespace,
        logger=logging.getLogger('reviewed_offline'),REQUIRED_COLUMNS={'open','high','low','close','volume'},__name__=__name__)
    defs=[];inputs=[]
    for path,names in DEFS.items():
        b=source(repo,path);tree=ast.parse(b)
        nodes=[n for n in tree.body if isinstance(n,(ast.FunctionDef,ast.ClassDef)) and n.name in names]
        if {n.name for n in nodes}!=set(names):raise ValueError('definition missing')
        # Isolate globals so identically named private indicator helpers stay original.
        mod=dict(ns)
        exec(compile(ast.Module(body=nodes,type_ignores=[]),path,'exec',flags=__future__.annotations.compiler_flag),mod)
        ns.update({n.name:mod[n.name] for n in nodes})
        defs.extend(dict(path=path,name=n.name,line=n.lineno,ast_sha256=sha(ast.dump(n).encode())) for n in nodes)
        inputs.append(dict(path=path,sha256=sha(b),git_blob_sha=subprocess.check_output(['git','-C',str(repo),'rev-parse',f'{PIN}:{path}'],text=True).strip()))
    raw=source(repo,'src/trend_pullback/config.py');cfg=ast.parse(raw)
    cl=next(n for n in cfg.body if isinstance(n,ast.ClassDef) and n.name=='StrategyParams')
    defaults={n.target.id:ast.literal_eval(n.value) for n in cl.body if isinstance(n,ast.AnnAssign)}
    sample=yaml.safe_load(source(repo,'configs/sample_backtest.yaml'))
    defaults.update(sample['strategy'])
    for path in ['src/trend_pullback/config.py','configs/sample_backtest.yaml','scripts/run_backtest.py']:
        b=source(repo,path);inputs.append(dict(path=path,sha256=sha(b),git_blob_sha=subprocess.check_output(['git','-C',str(repo),'rev-parse',f'{PIN}:{path}'],text=True).strip()))
    return ns,SimpleNamespace(**defaults),sample,defs,inputs

def broker_case(ns,name,direction=1,cash=1000.,entrybar=False,dual=False,stop=False,take=False,gap=False,entrygap=False,commission=.0006):
    bars=[[100,101,99,100,1],[100,101,99,100,1],[100,101,99,100,1],[100,101,99,100,1]]
    if entrybar:bars[1]=[100,101,90,100,1]
    if dual:bars[2]=[100,105,95,100,1]
    if stop:bars[2]=[100,101,97,99,1]
    if take:bars[2]=[100,105,99,103,1]
    if gap:bars[2]=[95,96,94,95,1];bars[3]=[95,96,94,95,1]
    if entrygap:
        bars[1]=[110,111,109,110,1];bars[2]=bars[1].copy();bars[3]=bars[1].copy()
    df=pd.DataFrame(bars,columns=['open','high','low','close','volume'],index=pd.date_range('2024-01-01',periods=4,freq='15min'))
    sig=df.copy();sig['long_signal']=False;sig['short_signal']=False
    sig.loc[sig.index[0],'long_signal' if direction>0 else 'short_signal']=True
    sig['low_prev']=99.;sig['high_prev']=101.;sig['atr']=.5
    records=[];states=[];trades=[]
    class Trace(ns['TrendPullbackStrategy']):
        def notify_order(self,order):
            records.append(dict(bar=len(self)-1,status=order.getstatusname(),ref=order.ref,
                is_entry=order.ref in self._entry_refs,created_price=float(order.created.price),
                price=float(order.executed.price),size=float(order.executed.size),commission=float(order.executed.comm)))
            super().notify_order(order)
        def notify_trade(self,trade):
            if trade.isclosed:trades.append(dict(gross=float(trade.pnl),net=float(trade.pnlcomm),bar=len(self)-1))
            super().notify_trade(trade)
        def next(self):
            super().next()
            states.append(dict(bar=len(self)-1,position=float(self.position.size),stop=self._active_stop,take=self._active_take,
                pending_entry=self._entry_order is not None,entry_refs=len(self._entry_refs),cash=float(self.broker.getcash()),value=float(self.broker.getvalue())))
    c=bt.Cerebro(stdstats=False);c.broker.setcash(cash);c.broker.setcommission(commission=commission)
    c.adddata(bt.feeds.PandasData(dataname=df));c.addstrategy(Trace,strategy_params=SimpleNamespace(atr_mul=2.,rr=2.),signals_df=sig,stake=1.)
    c.addanalyzer(ns['TradeListAnalyzer'],_name='source_trade_list')
    s=c.run()[0]
    return dict(name=name,synthetic=True,commission_rate=commission,initial_cash=cash,bars=bars,
        orders=records,states=states,closed=trades,original_analyzer=clean(s.analyzers.source_trade_list.get_analysis()),
        final_cash=float(c.broker.getcash()),final_value=float(c.broker.getvalue()),final_position=float(s.position.size))

def market(ns,sp,root,manifest,out):
    items=json.loads(Path(manifest).read_text());items=items if isinstance(items,list) else items['sources']
    item=next(x for x in items if x.get('month')=='2024-01' and x.get('kind')=='klines' and x.get('interval')=='1m')
    p=Path(root)/'klines/1m/BTCUSDT-1m-2024-01.zip';b=p.read_bytes()
    if sha(b)!=item['sha256']:raise ValueError('raw ZIP SHA')
    with zipfile.ZipFile(io.BytesIO(b)) as z:raw=z.read(z.namelist()[0])
    if sha(raw)!=item['csv_sha256']:raise ValueError('raw CSV SHA')
    cols=['start','open','high','low','close','volume','end','quote','trades','buyvol','buyquote','ignore']
    f=pd.read_csv(io.BytesIO(raw),header=0 if raw.startswith(b'open_time,') else None,names=cols)
    if len(f)!=44640 or not(f.start.diff().dropna()==60000).all() or f.start.duplicated().any():raise ValueError('minute integrity')
    v=f[['open','high','low','close','volume']].astype(float)
    if not np.isfinite(v.to_numpy()).all() or (v.iloc[:,:4]<=0).any().any() or (v.volume<0).any():raise ValueError('finite/positive')
    if ((v.high<v[['open','close','low']].max(axis=1))|(v.low>v[['open','close','high']].min(axis=1))).any():raise ValueError('OHLC range')
    v.index=pd.to_datetime(f.start.to_numpy(),unit='ms',utc=True)
    r=v.resample('15min',closed='left',label='left').agg(dict(open='first',high='max',low='min',close='last',volume='sum'))
    counts=v.close.resample('15min',closed='left',label='left').count()
    if len(r)!=2976 or not(counts==15).all():raise ValueError('aggregation')
    prefix=ns['compute_signals'](r.iloc[:1920],sp);full=ns['compute_signals'](r,sp)
    comparison=[]
    for col in prefix:
        a,b=prefix[col],full[col].iloc[:1920]
        eq=(a==b)|(a.isna()&b.isna())
        comparison.append(dict(column=col,compared_rows=len(a),past_changes=int((~eq).sum())))
    pd.DataFrame(comparison).to_csv(out/'actual_prefix_comparison.csv',index=False)
    selected=full[full.long_signal|full.short_signal].copy()
    selected['planned_long_risk_fraction']=(selected.close-selected.long_stop_candidate)/selected.close
    selected['planned_short_risk_fraction']=(selected.short_stop_candidate-selected.close)/selected.close
    selected.to_csv(out/'actual_BTC_signal_diagnostics.csv',index_label='bar_start_utc')
    return dict(source=item,manifest_sha256=sha(Path(manifest).read_bytes()),raw_minutes=len(f),complete15m=len(r),prefix15m=len(prefix),
        compared_columns=len(comparison),past_cell_changes=sum(x['past_changes'] for x in comparison),
        raw_long_signals=int(full.long_signal.sum()),raw_short_signals=int(full.short_signal.sum()),
        signal_days=int(selected.index.normalize().nunique()),market_days=31,
        sample_asset='ETHUSDT15mspot;thisisreusedBTCfuturescausality-only',source_strategy_settings=vars(sp),
        actual_market_entries_calculated=False,market_costed_returns_computed=False,
        prefix_csv_sha256=sha((out/'actual_prefix_comparison.csv').read_bytes()),signals_csv_sha256=sha((out/'actual_BTC_signal_diagnostics.csv').read_bytes()))

def main(a):
    out=Path(a.output);out.mkdir(parents=True,exist_ok=True);start=datetime.now(timezone.utc).isoformat()
    ns,sp,sample,defs,inputs=kernels(a.repo)
    cases=[broker_case(ns,'entrybar_stop_touch',entrybar=True),broker_case(ns,'long_dual',dual=True),
        broker_case(ns,'stop_only',stop=True),broker_case(ns,'target_only',take=True),broker_case(ns,'stop_gap',gap=True),
        broker_case(ns,'short_dual',direction=-1,dual=True),broker_case(ns,'margin',cash=50),broker_case(ns,'entry_gap',entrygap=True),
        broker_case(ns,'stop_only_double_commission',stop=True,commission=.0012),broker_case(ns,'target_only_double_commission',take=True,commission=.0012)]
    (out/'synthetic_broker_cases.json').write_text(json.dumps(clean(cases),indent=2,allow_nan=False)+'\n')
    with tempfile.TemporaryDirectory(prefix='pullback-loader-') as td:
        path=Path(td)/'numeric.csv'
        pd.DataFrame(dict(datetime=[1704067200,1704068100],open=[100,100],high=[101,101],low=[99,99],close=[100,100],volume=[1,1])).to_csv(path,index=False)
        parsed=ns['load_ohlcv'](path)
        loader=dict(unix_seconds_input=[1704067200,1704068100],parsed=[x.isoformat() for x in parsed.index],actual_separation_seconds=(parsed.index[1]-parsed.index[0]).total_seconds())
    summary=ns['compute_summary'](pd.DataFrame(),pd.DataFrame({'equity':[1000.,900.]}),1000.)
    market_result=market(ns,sp,a.data_root,a.manifest,out)
    engine={}
    for rel in ['__init__.py','cerebro.py','strategy.py','order.py','brokers/bbroker.py']:
        path=Path(bt.__file__).parent/rel;engine[rel]=sha(path.read_bytes())
    tree=subprocess.check_output(['git','-C',a.repo,'ls-tree','-r',PIN],text=True).splitlines()
    report=dict(version='silentbilly_2227152_execution_contract_v1',started_utc=start,completed_utc=datetime.now(timezone.utc).isoformat(),source_commit=PIN,
        source_tree=[dict(path=x.split('\t')[1],git_blob_sha=x.split('\t')[0].split()[2]) for x in tree],reviewed_definitions=defs,reviewed_source_files=inputs,
        actual_engine=dict(version=bt.__version__,kernel_sha256=engine,installed_package='backtrader==1.9.78.123'),
        original_sample_backtest=sample['backtest'],loader_fixture=loader,zero_closed_nonflat_equity_summary=clean(summary),actual_price_prefix=market_result,
        script_sha256=sha(Path(__file__).read_bytes()),protocol_sha256=sha(Path(__file__).with_name('pullback_contract_protocol_20261020.json').read_bytes()),
        valid_market_profitability_backtest=False,published_original_BTC_ledger_recovered=False,
        fullcost_double_result=None,expectancy_95_lower=None,PF=None,drawdown_R=None,tp1_wilson_lower=None,urgent_5min_rate=None,native_manual_execution=None,
        forward_started=False,eligible=False,production_changed=False,
        decision='exclude_thispublicrelease_asreplacement_evidence;executionandledgercontractnotqualified_notallpullbackstrategies')
    (out/'report.json').write_text(json.dumps(clean(report),indent=2,allow_nan=False)+'\n')
    print(json.dumps(dict(cases=[dict(name=c['name'],position=c['final_position'],closed=c['closed']) for c in cases],market=market_result,loader=loader,empty_summary=summary),indent=2))

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--repo',required=True);p.add_argument('--data-root',required=True);p.add_argument('--manifest',required=True);p.add_argument('--output',required=True);main(p.parse_args())
