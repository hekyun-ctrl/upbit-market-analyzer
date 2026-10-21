"""Frozen attribution of committed portfolio history; no notebook/optimizer execution."""
import json,csv,hashlib
from pathlib import Path
from decimal import Decimal as D
import pandas as pd
import numpy as np
root=Path('original1120');out=Path('attribution1120');out.mkdir(exist_ok=True)
files=['ETH_trading_strategies.ipynb','clean_data.py','download_data.py',
'data/raw/btcusdt_1h_raw.parquet','data/clean/btcusdt_1h_clean.parquet',
'data/raw/ethusdt_1h_raw.parquet','data/clean/ethusdt_1h_clean.parquet',
'output/tables/trend_following_history.csv','output/tables/trend_following_summary.csv',
'output/tables/trend_following_trade_pnl.csv']
provenance={f:dict(bytes=len((root/f).read_bytes()),sha256=hashlib.sha256((root/f).read_bytes()).hexdigest(),
 git_blob=hashlib.sha1(b'blob '+str(len((root/f).read_bytes())).encode()+b'\0'+(root/f).read_bytes()).hexdigest()) for f in files}
raws={a:pd.read_parquet(root/f'data/raw/{a}usdt_1h_raw.parquet') for a in ('btc','eth')}
clean={a:pd.read_parquet(root/f'data/clean/{a}usdt_1h_clean.parquet') for a in ('btc','eth')}
integrity={}
for a in ('btc','eth'):
 r=raws[a];c=clean[a];t=pd.to_datetime(r.open_time,utc=True);ct=pd.to_datetime(c.open_time,utc=True)
 assert t.is_unique and (t.diff().dropna()==pd.Timedelta(hours=1)).all()
 assert t.equals(ct)
 integrity[a]=dict(rows=len(r),start=str(t.iloc[0]),end=str(t.iloc[-1]),
 filled=int(c.filled_bar_flag.sum()),outlier=int(c.outlier_flag.sum()),
 clean_path_not_raw=int((abs(c.close_clean-c.close)>1e-8).sum()))
h=pd.read_csv(root/'output/tables/trend_following_history.csv')
t=pd.to_datetime(h.open_time,utc=True);assert t.is_unique and (t.diff().dropna()==pd.Timedelta(hours=1)).all()
def rollrate(p):
 delta=p.diff();cov=delta.cov(delta.shift(1))
 return .0005 if pd.isna(cov) or cov>=0 else float(np.clip(2*np.sqrt(-cov)/p.median(),.0001,.01))
rates={a:rollrate(h[a+'_price']) for a in ('btc','eth')}
checks=0;rows=[];prev={a:D(0) for a in ('btc','eth')}
for x in csv.DictReader((root/'output/tables/trend_following_history.csv').open()):
 now=dict(at=x['open_time']);gross=D(0);cost=D(0)
 for a in ('btc','eth'):
  theta=D(x['Theta_t_'+a]);ret=D(x[a+'_return']);turn=abs(theta-prev[a])
  g=theta*ret;c=turn*D(str(rates[a]));lagged=prev[a]*ret
  now[a]=dict(theta=str(theta),ret=str(ret),turnover=str(turn),original_gross=str(g),
    source_cost=str(c),source_net=str(g-c),same_path_lagged_gross=str(lagged),
    extra6bp_taker_net=str(g-c-turn*D('.0006')),
    extra12bp_taker_net=str(g-c-turn*D('.0012')))
  prev[a]=theta;gross+=g;cost+=c
 assert abs(gross-D(x['gross_pnl']))<D('1e-7');checks+=1
 assert abs(cost-D(x['slippage_cost']))<D('1e-7');checks+=1
 assert abs(gross-cost-D(x['net_pnl']))<D('1e-7');checks+=1
 rows.append(now)
def summ(a):
 ks=['original_gross','source_cost','source_net','same_path_lagged_gross','turnover','extra6bp_taker_net','extra12bp_taker_net']
 q={k:sum(D(x[a][k]) for x in rows) for k in ks}
 q['same_path_lagged_net']=q['same_path_lagged_gross']-q['source_cost']
 return {k:str(v) for k,v in q.items()}
summaries={a:summ(a) for a in ('btc','eth')}
summary=pd.read_csv(root/'output/tables/trend_following_summary.csv').iloc[0]
assert abs(sum(D(summaries[a]['source_net']) for a in ('btc','eth'))-D(str(summary.final_pnl_usdt)))<D('1e-6');checks+=1
ledger=pd.read_csv(root/'output/tables/trend_following_trade_pnl.csv')
assert len(ledger)==int(h.rebalanced.sum())
unclosed={a:float(h['Theta_t_'+a].iloc[-1]) for a in ('btc','eth')}
result=dict(protocol='BTC_component_committed_history_v1',source_commit='45dda879fbfb3bc6e8dbac46bd8605f01ca28c20',
 original_files=provenance,integrity=integrity,history_rows=len(h),history_start=str(t.iloc[0]),history_end=str(t.iloc[-1]),
 source_roll_rates=rates,checks=checks,components=summaries,portfolio_source_net=str(sum(D(summaries[a]['source_net']) for a in ('btc','eth'))),
 labelled_trade_rows=len(ledger),actual_rebalance_segments=len(ledger),
 ledger_segments_not_closed_BTC_campaigns=True,ending_asset_exposure=unclosed,
 causal_limit='Original current close signal/variance selects Theta_t then multiplies same close-to-close return; historical decomposition not an executable forward strategy. Lagged sensitivity holds source position path fixed, not repaired strategy.',
 preprocessing_limit='Entire cleaned sample quantiles define clipping and reconstruct tradable price; Roll cost estimate uses whole test window. No claim cleaned prices are actual fills.',
 missing=['Authenticated source-price provenance beyond committed files','Full actual funding and exchange fee inputs','Fixed initial stop/TP1 and manual fill contract','Independent future validation and native LBank book/delay'],
 full_cost_twice_net=None,TP1_Wilson=None,urgent_warning_rate=None,manual_fills=None,
 source_selection='Source n=12 already chosen by authors from12/24/36; no reselection or notebookexecution thisrun, no claim newOOS',
 original_strategy_price_backtest_executed=False,optimizer_executed=False,orders=0,messages=0,promotion=False,production_changed=False)
(out/'report.json').write_text(json.dumps(result,indent=2)+'\n')
(out/'BTC_ETH_cash_rows.json').write_text(json.dumps(rows,separators=(',',':'))+'\n')
print(json.dumps(result))
