"""Original published BTC tables: conditional turnover algebra, not price replay.

Only four explicitly reviewed AST definitions execute. No source top-level
imports/data loads/optimizer. All table rows retained, no strategy selection.
"""
from __future__ import annotations
import argparse,ast,hashlib,json,math,subprocess
from datetime import datetime,timezone
from pathlib import Path
from decimal import Decimal as D,localcontext
import numpy as np
import pandas as pd

PIN='5693993e108f2b3668bd21b1dd36ae31f79d8fcd'
NAMES=['build_day_night_sessions','position_array','max_drawdown_from_returns','backtest_combo']

def sha(p):return hashlib.sha256(Path(p).read_bytes()).hexdigest()
def source_kernel(repo):
    repo=Path(repo)
    if subprocess.check_output(['git','-C',str(repo),'rev-parse','HEAD'],text=True).strip()!=PIN:raise ValueError('source pin mismatch')
    tree=ast.parse((repo/'btc_eth_backtest.py').read_text())
    nodes=[n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name in NAMES]
    if {n.name for n in nodes}!=set(NAMES):raise ValueError('missing reviewed function')
    ns=dict(np=np,pd=pd,math=math,INITIAL_CAPITAL=100.,YEARS=list(range(2016,2026)))
    exec(compile(ast.Module(body=nodes,type_ignores=[]),'pinned_reviewed_functions','exec'),ns)
    return ns,[dict(name=n.name,line=n.lineno,ast_sha256=hashlib.sha256(ast.dump(n).encode()).hexdigest()) for n in nodes]

def unit_counts(change_events,turnover):
    n,t=int(change_events),int(turnover)
    if n!=change_events or t!=turnover:raise ValueError('noninteger counts')
    n2=t-n;n1=2*n-t
    if n1<0 or n2<0:raise ValueError('invalid {-1,0,1} turnover counts')
    return n1,n2

def reprice(gross_wealth,n1,n2,bps):
    c=bps/10000.
    if (n1 and c>=1) or (n2 and c>=.5):raise ValueError('nonpositive cost factor')
    # A zero exponent contributes zero; do not evaluate its unused log domain.
    return float(gross_wealth*math.exp((n1*math.log1p(-c) if n1 else 0.)+(n2*math.log1p(-2*c) if n2 else 0.)))

def breakeven(gross,n1,n2):
    if gross<=100 or not n1+n2:return None
    lo,hi=0.,4999.999999 if n2 else 9999.999999
    for _ in range(100):
        mid=(lo+hi)/2
        if reprice(gross,n1,n2,mid)>100:lo=mid
        else:hi=mid
    return (lo+hi)/2

def fixtures(repo):
    ns,defs=source_kernel(repo)
    s=pd.DataFrame({'trading_date':pd.date_range('2024-01-01',periods=4,tz='UTC'),
        'night_ret':[.01,-.03,.02,-.01],'day_ret':[-.02,.04,.01,.02]})
    cases=[]
    # Cost tests use synthetic sessions only, not author prices or new alpha.
    for night,day in [('long','long'),('cash','long'),('reversal','reversal')]:
        pos=np.column_stack([ns['position_array'](night,s.night_ret.shift()),ns['position_array'](day,s.day_ret.shift())]).reshape(-1)
        ret=np.column_stack([s.night_ret,s.day_ret]).reshape(-1)
        turns=np.abs(pos-np.r_[0.,pos[:-1]]);n1,n2=unit_counts(np.count_nonzero(turns),turns.sum())
        gross=ns['backtest_combo'](s,night,day,0.)['final_wealth']
        for bps in (1,2,8,16):
            value=ns['backtest_combo'](s,night,day,bps)['final_wealth']
            with localcontext() as ctx:
                ctx.prec=40;cash=D('100')
                for p,r,t in zip(pos,ret,turns):
                    cash*=(1+D(str(float(p)))*D(str(float(r))))*(1-D(str(bps))/D('10000')*D(str(float(t))))
            cases.append(dict(night=night,day=day,bps=bps,n1=n1,n2=n2,
                original=value,repriced=reprice(gross,n1,n2,bps),Decimal=str(cash),change_events=int(np.count_nonzero(turns)),
                initial_nonzero=int(np.count_nonzero(pos)),terminal_marked_position=float(pos[-1])))
    # Source shift controls: full append cannot change already-known lag input.
    old=ns['position_array']('reversal',s.night_ret.shift())
    extended=pd.concat([s.night_ret,pd.Series([.9,-.8])],ignore_index=True)
    new=ns['position_array']('reversal',extended.shift())[:len(s)]
    return dict(cash_cases=cases,past_position_changes=int(np.count_nonzero(old!=new)),
        lagged_positions=old.tolist(),source_model='1+net=(1+p*r)*(1-c*abs(p-prevp));not native account cash'),defs

def inventory(repo):
    rows=[]
    for line in subprocess.check_output(['git','-C',str(repo),'ls-tree','-r','HEAD'],text=True).splitlines():
        meta,path=line.split('\t',1);rows.append(dict(path=path,git_blob_sha=meta.split()[2],sha256=sha(Path(repo)/path)))
    return rows

def main(a):
    repo,out=Path(a.repo),Path(a.output);out.mkdir(parents=True,exist_ok=True)
    start=datetime.now(timezone.utc).isoformat()
    f,defs=fixtures(repo)
    full=pd.read_csv(repo/'paper_outputs/tables/cutoff_strategy_full_results.csv')
    core=pd.read_csv(repo/'paper_outputs/tables/btc_eth_all_metrics.csv')
    t=full[full.Asset=='BTC'].copy();c=core[core.coin=='BTC'].copy()
    if len(full)!=1800 or len(core)!=1050 or len(t)!=900 or len(c)!=525:raise ValueError('table dimensions')
    norm=lambda x:str(x).lower().replace('momentum','trend')
    t['night']=t.Night_Strategy.map(norm);t['day']=t.Day_Strategy.map(norm)
    c['night']=c.night_strategy.map(norm);c['day']=c.day_strategy.map(norm)
    keys=['Cutoff_Hour','night','day']
    if t.duplicated(keys+['Transaction_Cost']).any():raise ValueError('duplicate strategy/cost')
    rows=[];errors=[]
    for key,g in t.groupby(keys,sort=True):
        if set(g.Transaction_Cost)!={0,1,2}:raise ValueError('cost schedule missing')
        b=g[g.Transaction_Cost==0].iloc[0]
        n1,n2=unit_counts(b.Number_of_Trades,b.Total_Turnover)
        if not(g.Number_of_Trades==b.Number_of_Trades).all() or not(g.Total_Turnover==b.Total_Turnover).all():raise ValueError('cost alters book')
        entry=dict(cutoff=int(key[0]),night=key[1],day=key[2],source_gross_wealth=float(b.Terminal_Wealth),
            source_change_events=int(b.Number_of_Trades),source_turnover=float(b.Total_Turnover),n1=n1,n2=n2,
            source_dates=int(b.N_Trading_Dates),change_events_per_day=float(b.Number_of_Trades/b.N_Trading_Dates),
            economic_closed_campaigns=None,opportunity_days=None,break_even_bps=breakeven(b.Terminal_Wealth,n1,n2))
        for rate in (0,1,2,8,16):entry[f'conditional_wealth_{rate}bps']=reprice(b.Terminal_Wealth,n1,n2,rate)
        for z in g.itertuples():
            predicted=entry[f'conditional_wealth_{int(z.Transaction_Cost)}bps']
            relative=abs(predicted-z.Terminal_Wealth)/max(1.,abs(z.Terminal_Wealth))
            errors.append(dict(cutoff=int(key[0]),night=key[1],day=key[2],bps=int(z.Transaction_Cost),source=z.Terminal_Wealth,predicted=predicted,relative_error=relative))
        rows.append(entry)
    matrix=pd.DataFrame(rows);matrix.to_csv(out/'all300_BTC_conditional_costs.csv',index=False)
    residual=pd.DataFrame(errors);residual.to_csv(out/'all900_BTC_cost_reconciliation.csv',index=False)
    core_compare=c.merge(t,left_on=['cutoff','night','day','transaction_cost_bps'],right_on=['Cutoff_Hour','night','day','Transaction_Cost'],validate='one_to_one')
    if len(core_compare)!=525:raise ValueError('lost core rows')
    coreerr=float((abs(core_compare.final_wealth-core_compare.Terminal_Wealth)/np.maximum(1.,abs(core_compare.final_wealth))).max())
    if residual.relative_error.max()>1e-8 or coreerr>1e-8:raise ValueError('table/cost mismatch;stop before using repricing')
    stats={}
    active=matrix[~((matrix.night=='cash')&(matrix.day=='cash'))]
    for rate in (0,1,2,8,16):
        wealth=active[f'conditional_wealth_{rate}bps'];stats[str(rate)]=dict(total=len(active),positive=int((wealth>100+1e-8).sum()),negative=int((wealth<100-1e-8).sum()),flat=int((abs(wealth-100)<=1e-8).sum()))
    preferred=matrix[(matrix.cutoff==8)&(matrix.night=='reversal')&(matrix.day=='reversal')].iloc[0].to_dict()
    inference=pd.read_csv(repo/'paper_outputs/tables/preferred_vs_buyhold_tests.csv')
    inference=inference[inference.asset=='BTC'];inference.to_csv(out/'author_BTC_all_inference_periods.csv',index=False)
    # Originals remain fully versioned at source_commit; record every blob/SHA
    # rather than duplicate unrelated ETH tables into this research repository.
    report=dict(version='daynight_original_BTC_cost_algebra_v1',source_commit=PIN,source_url='https://github.com/wzf01195010-png/Crypto-day-night-effects/tree/'+PIN,
        started_utc=start,completed_utc=datetime.now(timezone.utc).isoformat(),script_sha256=sha(__file__),
        protocol_sha256=sha(Path(__file__).with_name('daynight_cost_protocol_20261020.json')),
        source_tree=inventory(repo),reviewed_definitions=defs,fixtures=f,
        table_rows=dict(full_all_assets=1800,core_all_assets=1050,full_BTC=900,core_BTC=525,unique_BTC_cells=300),
        maximum_cost_relative_error=float(residual.relative_error.max()),maximum_core_full_overlap_relative_error=coreerr,
        unique_active_BTC_cells_wealth_above100_by_bps=stats,author_selected_preferred_BTC=preferred,
        known_author_positive_outcome_seen_before_fixation='preferredBTC0/1bps alreadyseen;other300cell outcomes notinspectedbeforerun',
        original_raw_price_SHA=None,original_price_replayed=False,original_closed_ledger_recovered=False,
        economic_fullcost_double_result=None,net_expectancy_95_lower=None,profit_factor=None,drawdown_R=None,tp1_wilson_lower=None,urgent_5min_rate=None,native_manual_execution_validated=False,
        forward_started=False,eligible=False,production_changed=False,
        decision='not_promoted;conditional_summaryalgebra_only_missingrawprice_and_manualboundedriskcontract',
        limitations=['8/16bpsdiagnosticextendsoriginalturnoverformula;notactual0.06%fee/0.02%fills orfunding,notfullcostgate.',
            'All300cells multiple-selectedcorrelatedhistoricalviews,not300independentforwardtests.',
            'Originaltrade_count ispositionchangeevents,notclosedcampaigns/entryalerts oropportunitydays.',
            'Originalterminalwealth marks openpositionwithoutendliquidationcost;notrealizedcashledger.',
            'PublishedbootstrapCIvsbuyhold notourabsoluteexpectedRlowerbound;selectionamong25x12notcuredbylimitedHolmgroup.',
            'Source1xshortsimpleperiodreturn/2xturnovercost has nofunding,borrow,SLTP1,urgentwarn,book/manualdelay.',
            'AuthorKrakenrawnotpublished;officialarchiveexistencedoesnotconfirmexactauthorfile orcontinuousno-tradehourtreatment.',
            'No new alpha rules/stop/TP/optimizer orriskincrease;no profitcertification or deadline.'])
    (out/'report.json').write_text(json.dumps(report,indent=2,allow_nan=False)+'\n')
    print(json.dumps({k:report[k] for k in ['table_rows','maximum_cost_relative_error','maximum_core_full_overlap_relative_error','unique_active_BTC_cells_wealth_above100_by_bps','author_selected_preferred_BTC']},indent=2))

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--repo',required=True);p.add_argument('--output',required=True);main(p.parse_args())
