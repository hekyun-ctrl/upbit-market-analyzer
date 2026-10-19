"""Matched-entry exit experiment with a frozen aggressive-flow ablation.

All reused periods are exploratory. No live alerts, orders, or promotion.
"""
import argparse,json,math
from pathlib import Path
import numpy as np
import pandas as pd
from sklearn.ensemble import HistGradientBoostingRegressor
from research.long_history.evaluate import (FEATURES,FEE,SLIP,load_archives,
    verify_manifests,unit_net,score,digest)
from research.long_history.entry_location import funding_cost

POLICIES=('original_target','original_target_with_guard','cap_0.5R_with_guard','cap_1.0R_with_guard')
FLOW=['flow5','flow15','flow60','flow_change','quote_relative','trades_relative','average_trade_quote','flow_price_alignment']


def target_for_net(d,entry,net_value):
    e=entry*(1+d*SLIP)
    return (net_value+(d+FEE)*e)/((d-FEE)*(1-d*SLIP))


def flow_frame(one):
    f=one.copy()
    if ((f['buy_volume']<0)|(f['buy_volume']>f['volume']+1e-6)|(f['trades']<0)).any():
        raise ValueError('invalid aggressive-flow fields')
    pieces=[]
    for _,s in f.groupby((f['start'].diff()!=60).cumsum()):
        s=s.copy();net=2*s['buy_volume']-s['volume']
        for n in (5,15,60):s[f'flow{n}']=net.rolling(n).sum()/s['volume'].rolling(n).sum().replace(0,np.nan)
        s['flow_change']=s['flow5']-s['flow5'].shift(5)
        q=s['quote_volume'].rolling(5).sum();tr=s['trades'].rolling(5).sum()
        s['quote_relative']=q/q.shift(5).rolling(60).median().replace(0,np.nan)
        s['trades_relative']=tr/tr.shift(5).rolling(60).median().replace(0,np.nan)
        s['average_trade_quote']=q/tr.replace(0,np.nan)
        s['flow_price_alignment']=s['flow5']*np.sign(s['close']-s['close'].shift(5))
        pieces.append(s[['start']+FLOW])
    return pd.concat(pieces).set_index('start')


def follow(e,m,ft,fr,policy,max_minutes=120):
    starts,op,hi,lo,cl=m;d=int(e['side']);t=int(e['entry_at']);entry=e['entry'];stop=e['stop'];risk=e['unit_net_risk'];atr=e['atr']
    target=e['target']
    if policy>=2:
        cap=target_for_net(d,entry,risk*(.5 if policy==2 else 1.))
        target=min(target,cap) if d==1 else max(target,cap)
    i=int(np.searchsorted(starts,t));guard=None;guard_bar=-1;peak=0.;reason='censored_end';exit_price=None;exit_at=None
    for j in range(i,min(i+max_minutes,len(starts))):
        if starts[j]!=t+(j-i)*60:reason='censored_gap';exit_at=int(starts[j-1]+60);break
        active=(max(stop,guard) if d==1 else min(stop,guard)) if guard is not None else stop
        is_guard=guard is not None and d*(active-stop)>0
        if d*(op[j]-active)<=0:exit_price=op[j];reason='guard_gap' if is_guard else 'gap_stop'
        elif d*(op[j]-target)>=0:exit_price=target;reason='target'
        else:
            sh=d*((lo[j] if d==1 else hi[j])-active)<=0;th=d*((hi[j] if d==1 else lo[j])-target)>=0
            if sh:exit_price=active;reason='guard_exit' if is_guard else 'ambiguous_stop_first' if th else 'stop'
            elif th:exit_price=target;reason='target'
        if exit_price is not None:exit_at=int(starts[j]+60);break
        if policy:
            fund=funding_cost(ft,fr,starts,op,t,int(starts[j]+60),d,entry,cl[j])
            peak=max(peak,unit_net(d,entry,cl[j],fund)/risk)
            candidate=target_for_net(d,entry,.25*peak*risk+fund);bar=int((starts[j]+60)//300)
            if (peak>=.5 and d*(cl[j]-candidate)>=.5*atr
                and (guard is None or (bar>guard_bar and d*(candidate-guard)>=.25*atr))):
                guard=candidate;guard_bar=bar  # only future minutes may trigger it
    if exit_price is None and reason=='censored_end' and min(i+max_minutes,len(starts))-i==max_minutes:
        exit_price=cl[i+max_minutes-1];exit_at=int(starts[i+max_minutes-1]+60);reason='time_exit'
    if exit_at is None:exit_at=int(starts[-1]+60)
    fund=funding_cost(ft,fr,starts,op,t,exit_at,d,entry,exit_price or entry)
    return dict(policy=POLICIES[policy],exit_at=exit_at,exit=exit_price,reason=reason,
        net_r=unit_net(d,entry,exit_price,fund)/risk if exit_price is not None else np.nan,
        stress_r=unit_net(d,entry,exit_price,fund,2.)/risk if exit_price is not None else np.nan,
        target_hit=reason=='target',early_stop_5m=reason in ('stop','gap_stop','ambiguous_stop_first') and exit_at-t<=300)


def run(folder,labels,selected,output):
    folder=Path(folder);out=Path(output);out.mkdir(parents=True,exist_ok=True)
    sources=verify_manifests(folder);one=load_archives(folder,'1m');fund=load_archives(folder,'5m','fundingRate').sort_values('calc_time')
    bad=json.loads(Path(__file__).with_name('integrity_final_2024_2026.json').read_text())['quarantined_starts']
    one=one[~(one['start']//300*300).isin(bad)].reset_index(drop=True)
    m=tuple(one[k].to_numpy() for k in ('start','open','high','low','close'));ft=(fund['calc_time'].to_numpy()/1000).astype('int64');fr=fund['last_funding_rate'].to_numpy()
    base=pd.read_csv(labels);flow=flow_frame(one);base=base.join(flow.reindex((base['start']+240).to_numpy()).reset_index(drop=True))
    # The flow lookup label is not a model feature; the candidate timestamp stays unchanged.
    records=[]
    for idx,e in enumerate(base.to_dict('records')):
        for policy in range(4):records.append(dict(candidate_id=idx,**follow(e,m,ft,fr,policy)))
    exits=pd.DataFrame(records);baseline=exits[exits.policy==POLICIES[0]].sort_values('candidate_id')
    expected=base.net_r.to_numpy();actual=baseline.net_r.to_numpy()
    if not np.array_equal(np.isnan(expected),np.isnan(actual)) or not np.allclose(expected,actual,rtol=1e-8,atol=1e-8,equal_nan=True):
        raise ValueError('unchanged baseline replay failed to match original ledger')
    table=exits.merge(base.drop(columns=['exit_at','exit','reason','net_r','stress_r','target_hit','early_stop_5m']),left_on='candidate_id',right_index=True)
    prior=pd.read_csv(selected);prior=prior[prior.evaluation.str.endswith(':all_unfiltered')]
    keys={(int(r.entry_at),int(r.family),int(r.side)) for r in prior.itertuples()}
    table['baseline_selected']=[(int(r.entry_at),int(r.family),int(r.side)) in keys for r in table.itertuples()]
    predictions={};fits={}
    for year in (2025,2026):
        cutoff=int(pd.Timestamp(f'{year}-01-01',tz='UTC').timestamp());end=int(pd.Timestamp(f'{year+1}-01-01',tz='UTC').timestamp())
        for kind,features in [('price',FEATURES),('price_and_flow',FEATURES+FLOW)]:
            for policy in POLICIES[1:]:
                subset=table[table.policy==policy]
                fit=subset[(subset.entry_at<cutoff-86400)&(subset.exit_at<cutoff-86400)&np.isfinite(subset.net_r)&np.isfinite(subset[features]).all(axis=1)]
                test=subset[(subset.entry_at>=cutoff)&(subset.entry_at<end)&np.isfinite(subset[features]).all(axis=1)]
                model=HistGradientBoostingRegressor(max_iter=150,learning_rate=.05,max_leaf_nodes=7,min_samples_leaf=100,l2_regularization=1.,early_stopping=False,random_state=20261020)
                model.fit(fit[features],fit.net_r);pred=model.predict(test[features])
                for candidate,value in zip(test.candidate_id,pred):predictions[(year,kind,int(candidate),policy)]=float(value)
                fits[f'{year}:{kind}:{policy}']=dict(training_cases=len(fit),test_cases=len(test),max_prediction=float(pred.max()))
    summaries={};ledgers=[]
    for year in (2025,2026):
        start=int(pd.Timestamp(f'{year}-01-01',tz='UTC').timestamp());end=min(int(pd.Timestamp(f'{year+1}-01-01',tz='UTC').timestamp()),int(one.start.iloc[-1]+60))
        part=table[(table.entry_at>=start)&(table.entry_at<end)&table.baseline_selected].copy()
        part.loc[part.exit_at>end,['net_r','stress_r']]=np.nan
        variants=[(policy,part[part.policy==policy]) for policy in POLICIES]
        for kind in ('price','price_and_flow'):
            selected_rows=[]
            for candidate,g in part.groupby('candidate_id'):
                ranked=[(predictions.get((year,kind,int(candidate),p),-np.inf),p) for p in POLICIES[1:]]
                value,policy=max(ranked)
                if value>.10:selected_rows.append(g[g.policy==policy])
            chosen=pd.concat(selected_rows) if selected_rows else part.iloc[:0]
            variants.append((kind+'_model',chosen))
        for name,subset in variants:
            summaries[f'{year}:{name}']=score(subset,start,end)
            copy=subset.copy();copy['evaluation']=f'{year}:{name}';ledgers.append(copy)
    pd.concat(ledgers).to_csv(out/'matched_selected.csv.gz',index=False,compression={'method':'gzip','mtime':0})
    table.to_csv(out/'all_candidate_exits.csv.gz',index=False,compression={'method':'gzip','mtime':0})
    report=dict(protocol_sha256=digest(Path(__file__).with_name('exit_intervention_protocol_20261020.json')),
        all_periods_exploratory=True,baseline_replay_matches=True,candidate_count=len(base),summaries=summaries,model_fits=fits,
        source_manifest=sources,production_changed=False,lbank_flow_or_fills_verified=False,
        outputs={p.name:dict(sha256=digest(p),bytes=p.stat().st_size) for p in out.glob('*.csv.gz')})
    (out/'exit_intervention_report.json').write_text(json.dumps(report,indent=2,allow_nan=False)+'\n')
    print(json.dumps({k:v for k,v in report.items() if k!='source_manifest'},indent=2,allow_nan=False),flush=True)


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--folder',required=True);p.add_argument('--labels',required=True)
    p.add_argument('--selected',required=True);p.add_argument('--output',required=True);a=p.parse_args()
    run(a.folder,a.labels,a.selected,a.output)
