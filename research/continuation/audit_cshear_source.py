"""Pinned original-source contract probes; not a strategy profitability backtest.

Execute reviewed class/function definitions only. Never import the original
package, unpickle its data, start a bot, or infer LBank performance from this.
"""
import argparse
import ast
import bisect
import csv
from dataclasses import dataclass,field
from datetime import datetime,timezone
from decimal import Decimal
import hashlib
import io
import json
import math
from pathlib import Path
import subprocess
import sys
import types
import zipfile

COMMIT='72f293c19a61e01507f322595e3982da00f0932d'
VERSION='cshear_72f293_source_contract_v1'
SELECTED={
    'analog/backfill.py':['CandleData','FundingSnapshot'],
    'analog/evaluators.py':['HistoricalData','_make_funding_arb'],
    'analog/exit_analysis.py':['infer_direction'],
    'analog/tp_trail_backtest.py':['simulate_exit_tp_to_trail','infer_direction'],
    'analog/strategies.py':['make_rsi_regime_reversion'],
}

def sha(raw):return hashlib.sha256(raw).hexdigest()
def canonical(x):return json.dumps(x,sort_keys=True,separators=(',',':')).encode()

def kernel(repo):
    commit=subprocess.check_output(['git','rev-parse','HEAD'],cwd=repo,text=True).strip()
    if commit!=COMMIT:raise ValueError('unreviewed source commit')
    module=types.ModuleType('reviewed_cshear_contract_kernel');sys.modules[module.__name__]=module
    ns=module.__dict__;ns.update(dataclass=dataclass,field=field,bisect=bisect,math=math,COMMISSION=.0006)
    functions={};manifest=[]
    for filename,names in SELECTED.items():
        raw=(repo/filename).read_bytes();source=raw.decode();tree=ast.parse(source)
        nodes=[]
        for name in names:
            found=[n for n in tree.body if isinstance(n,(ast.FunctionDef,ast.ClassDef)) and n.name==name]
            if len(found)!=1:raise ValueError('selected reviewed definition changed')
            node=found[0];nodes.append(node)
            manifest.append(dict(file=filename,name=name,source_file_sha256=sha(raw),
                definition_sha256=sha(ast.get_source_segment(source,node).encode())))
        # All other top-level statements/imports/entrypoints are deliberately omitted.
        selected=ast.Module(body=[ast.ImportFrom(module='__future__',names=[ast.alias(name='annotations')],level=0)]+nodes,type_ignores=[])
        ast.fix_missing_locations(selected);exec(compile(selected,filename,'exec'),ns)
        for name in names:functions[(filename,name)]=ns[name]
    return ns,functions,manifest

def candle(ns,t=0,o=100,h=100,l=100,c=100):
    return ns['CandleData'](int(t*1000),float(o),float(h),float(l),float(c),1.)

def fixture_probes(repo):
    ns,f,definitions=kernel(repo);C=ns['HistoricalData'];fund=ns['FundingSnapshot']
    infer=f[('analog/exit_analysis.py','infer_direction')]
    infer_tp=f[('analog/tp_trail_backtest.py','infer_direction')]
    initial=candle(ns);later=candle(ns,14400,c=120,h=120)
    past=C({'BTC':[initial]},{});full=C({'BTC':[initial,later]}, {})
    query=10800
    lookups=dict(query=query,prefix_label=past.get_candle_at('BTC',query).timestamp_ms/1000,
        full_label=full.get_candle_at('BTC',query).timestamp_ms/1000,
        prefix_close=past.get_recent_candles('BTC',query,1)[0].close,
        full_close=full.get_recent_candles('BTC',query,1)[0].close,
        future_label_selected=full.get_candle_at('BTC',query).timestamp_ms/1000>query)
    tie=dict(at_label=full.get_candle_at('BTC',7200).timestamp_ms/1000,
             index_label=full._candle_data['BTC'][full.get_candle_index('BTC',7200)].timestamp_ms/1000)
    first=[fund('BTC',.0001,0)];future=first+[fund('BTC',.0009,10800000)]
    funding_append=dict(query=3600,prefix=C({'BTC':[initial]},{'BTC':first}).get_funding_at(3600)['BTC'],
        full=C({'BTC':[initial]},{'BTC':future}).get_funding_at(3600)['BTC'])
    samples=[fund('BTC',rate,i*3600000) for i,rate in enumerate([.0001,.0002,.0009])]
    funding_order=dict(chronological=C({}, {'BTC':samples}).get_funding_at(0)['BTC'],
        reversed=C({}, {'BTC':list(reversed(samples))}).get_funding_at(0)['BTC'],
        arithmetic_mean=sum(x.rate for x in samples)/3)
    # Original funding factory always intends SHORT here: positive rate > threshold.
    directions=[]
    for close in (100.02,100.10):
        data=C({'BTC':[initial,candle(ns,14400,c=close,h=close)]},{'BTC':[fund('BTC',.0008,0)]})
        ev=f[('analog/evaluators.py','_make_funding_arb')](data)(0,4)
        directions.append(dict(future_close=close,source_expected_side=-1,evaluator_pnl=ev,
            inferred_side_exit=infer(data,'BTC',0,ev,4),inferred_side_tp=infer_tp(data,'BTC',0,ev,4)))
    tiny=[]
    for close in (100.005,100.02):
        data=C({'BTC':[initial,candle(ns,14400,c=close,h=close)]},{})
        pnl=data.forward_return('BTC',0,4)-.0006
        tiny.append(dict(future_close=close,explicit_unit_long_pnl=pnl,
            inferred=infer(data,'BTC',0,pnl,4),expected_for_unit_long=1))
    sample_end=C({'BTC':[initial,later]},{}).simulate_exit('BTC',0,1,max_hold_hours=48)
    gap_data=C({'BTC':[initial,candle(ns,14400),candle(ns,43200,c=120,h=120)]},{})
    gap=dict(requested_forward_hours=4,actual_timestamp_span_hours=8,
        source_forward_return=gap_data.forward_return('BTC',14400,4),
        source_exit=gap_data.simulate_exit('BTC',14400,1,max_hold_hours=4))
    stop_gap=C({'BTC':[initial,candle(ns,14400,o=90,h=91,l=89,c=90)]},{}).simulate_exit('BTC',0,1,stop_loss=.02,max_hold_hours=4)
    both=C({'BTC':[initial,candle(ns,14400,o=100,h=110,l=90,c=100)]},{}).simulate_exit('BTC',0,1,stop_loss=.02,take_profit=.05,max_hold_hours=4)
    ambiguous=C({'BTC':[initial,candle(ns,14400,o=100,h=110,l=99,c=109.8)]},{})
    trail=f[('analog/tp_trail_backtest.py','simulate_exit_tp_to_trail')](ambiguous,'BTC',0,1,stop_loss=.02,take_profit=.05,trail_after_tp=.005,max_hold_hours=4)
    # Two monotonic tick paths with identical OHLC; point-risk units match source.
    def path_exit(path):
        peak=0.;activated=False
        for price in path:
            move=(price-100)/100
            if move<=-.02:return -.02-.0006,'stop_loss'
            peak=max(peak,move);activated=activated or peak>=.05
            if activated and peak-move>=.005:return max(peak-.005,0)-.0006,'tp_trail'
        return (path[-1]-100)/100-.0006,'time_exit_post_tp'
    paths=dict(source=trail,high_then_low=path_exit([100,110,99,109.8]),low_then_high=path_exit([100,99,110,109.8]),
               exact_intrabar_order_known=False,systematic_profit_bias_estimated=False)
    class RSIInputs:
        primary='BTC'
        def b(self,n):return n
        def rsi(self,*args):return 24.
        def sma(self,*args):return 90.
        def get_candle_at(self,*args):return initial
        def forward_return(self,*args):return .01
    rsi_out=f[('analog/strategies.py','make_rsi_regime_reversion')](RSIInputs())(0,4)
    return dict(selected_definitions=definitions,nearest_lookup=lookups,tie_lookup=tie,
        funding_future_append=funding_append,funding_input_order=funding_order,
        funding_direction_recovery=directions,small_return_direction_recovery=tiny,
        sample_end_source_exit=sample_end,gap_time_compression=gap,stop_gap_source_exit=stop_gap,
        both_touch_stop_first_control=both,tp_trail_same_OHLC_paths=paths,
        source_rsi24_uptrend_fixture_pnl=rsi_out,
        interpretation='Bounded source-contract counterexamples/controls, not measured market frequencies or an estimate of headline profit impact.')

def inventory(repo):
    entries=subprocess.check_output(['git','ls-tree','-rz','HEAD'],cwd=repo).split(b'\0');rows=[]
    for entry in filter(None,entries):
        metadata,path=entry.split(b'\t',1);mode,kind,oid=metadata.decode().split();name=path.decode()
        if kind!='blob':raise ValueError('unexpected non-blob tracked entry')
        raw=subprocess.check_output(['git','cat-file','blob',oid],cwd=repo)
        rows.append(dict(path=name,mode=mode,git_blob_sha=oid,bytes=len(raw),sha256=sha(raw)))
    return dict(commit=COMMIT,tracked=rows,tracked_count=len(rows),
        public_data_or_ledger_paths=[x['path'] for x in rows if x['path'].endswith(('.csv','.parquet','.pkl','.jsonl','.feather'))])

def real_price_prefix(repo,archive,manifest):
    record=[x for x in json.loads(manifest.read_text())['archives'] if x['month']=='2024-01' and x['kind']=='klines'][0]
    raw=archive.read_bytes()
    if sha(raw)!=record['sha256']:raise ValueError('minute ZIP SHA changed')
    with zipfile.ZipFile(io.BytesIO(raw)) as z:csvraw=z.read(z.namelist()[0])
    lines=csv.reader(io.StringIO(csvraw.decode()));first=next(lines);records=[]
    if first[0].isdigit():records.append(first)
    records.extend(lines);buckets={};starts=[]
    for row in records:
        t=int(row[0])//1000;o,h,l,c,v=map(float,row[1:6]);starts.append(t)
        if not all(math.isfinite(x) for x in (o,h,l,c,v)) or min(o,h,l,c)<=0 or v<0 or h<max(o,l,c) or l>min(o,h,c):raise ValueError('invalid OHLCV')
        bucket=t//14400*14400;b=buckets.setdefault(bucket,[o,h,l,c,0.,0])
        b[1]=max(b[1],h);b[2]=min(b[2],l);b[3]=c;b[4]+=v;b[5]+=1
    if len(starts)!=44640 or any(b-a!=60 for a,b in zip(starts,starts[1:])):raise ValueError('frozen complete month not contiguous')
    ns,_,_=kernel(repo);bars=[]
    for t,values in sorted(buckets.items()):
        if values[5]!=240:raise ValueError('incomplete4hbar')
        o,h,l,c,v,_=values;bars.append(ns['CandleData'](t*1000,o,h,l,c,v))
    C=ns['HistoricalData'];full=C({'BTC':bars},{});rows=[]
    for i in range(len(bars)-1):
        # Frozen 3h offset, no price-dependent timestamp or query selection.
        query=bars[i].timestamp_ms/1000+10800;prefix=C({'BTC':bars[:i+1]}, {})
        before=prefix.get_recent_candles('BTC',query,1)[0];after=full.get_recent_candles('BTC',query,1)[0]
        at_label=bars[i].timestamp_ms/1000
        rows.append(dict(query=query,prefix_selected_label=before.timestamp_ms/1000,
            full_selected_label=after.timestamp_ms/1000,prefix_close=before.close,full_close=after.close,
            later_label_selected=after.timestamp_ms/1000>query,past_price_changed=before.close!=after.close,
            exact_label_control_unchanged=prefix.get_candle_at('BTC',at_label)==full.get_candle_at('BTC',at_label)))
    return dict(source_url=record['url'],checksum_url=record['checksum_url'],zip_sha256=sha(raw),
        csv_sha256=sha(csvraw),minute_rows=len(starts),missing_or_duplicate_minutes=0,bars_4h=len(bars),
        queries=len(rows),queries_using_later_timestamp=sum(x['later_label_selected'] for x in rows),
        past_query_price_changes=sum(x['past_price_changed'] for x in rows),
        exact_label_control_changes=sum(not x['exact_label_control_unchanged'] for x in rows),
        rows=rows,reused_observed_prices=True,native_LBank=False,profitability_backtest_performed=False,
        scope='Lookup contract at frozen non-grid queries. Not market occurrence rates; no claim that every original on-grid strategy return is contaminated. Candle-label availability is not inferred from price.')

def unit_costs():
    rows=[]
    for side in (1,-1):
        for m in (1,2):
            for move in (Decimal('.05'),Decimal('-.02')):
                e=Decimal(100);d=Decimal(side);mult=Decimal(m)
                x=e*(1+d*move);ef=e*(1+d*Decimal('.0002')*mult);xf=x*(1-d*Decimal('.0002')*mult)
                fee=Decimal('.0006')*mult*(ef+xf);reserve=e*Decimal('.0001')*mult
                rows.append(dict(side=side,cost_multiple=m,price_move_fraction=str(move),
                    net_cash=str(d*(xf-ef)-fee-reserve),fee_cash=str(fee),funding_reserve_cash=str(reserve)))
    return dict(rows=rows,actual_native_cost_or_funding_verified=False,market_PnL_calculated=False,
                interpretation='Hypothetical same-reference cash checks only. Source flat0.0006 return charge is not full fees+slippage+funding.')

def main():
    p=argparse.ArgumentParser();p.add_argument('--repo',type=Path,required=True);p.add_argument('--minute-archive',type=Path,required=True)
    p.add_argument('--output-dir',type=Path,required=True);a=p.parse_args()
    if a.output_dir.exists():raise FileExistsError(a.output_dir)
    start=datetime.now(timezone.utc).isoformat();root=Path(__file__).resolve().parents[2]
    protocol=Path(__file__).with_name('cshear_source_protocol_20261020.json')
    result=dict(version=VERSION,started_utc=start,source_inventory=inventory(a.repo),fixtures=fixture_probes(a.repo),
        real_price_prefix=real_price_prefix(a.repo,a.minute_archive,root/'research/long_history/results/source_manifest_2024_2026_1m.json'),
        hypothetical_unit_costs=unit_costs(),implementation_sha256=sha(Path(__file__).read_bytes()),protocol_sha256=sha(protocol.read_bytes()),
        source_market_profitability_backtest_performed=False,source_headline_profit_impact_measured=False,
        source_original_data_or_closed_ledger_recovered=False,market_PF=None,net_expectancy_lower=None,
        TP1_Wilson_lower=None,actual_urgent_warning_rate=None,native_manual_fill_verified=False,
        actual_max_drawdown_R=None,full_historical_cost_stress=None,eligible=False,forward_started=False,
        production_changed=False,decision='exclude_this_release_as_replacement_evidence_missing_BTC_ledger_fullcost_and_execution_contract')
    result['completed_utc']=datetime.now(timezone.utc).isoformat();a.output_dir.mkdir(parents=True)
    (a.output_dir/'report.json').write_text(json.dumps(result,indent=2)+'\n')
    print(json.dumps(dict(version=VERSION,selected_definitions=len(result['fixtures']['selected_definitions']),
        real_queries=result['real_price_prefix']['queries'],later_timestamp_lookups=result['real_price_prefix']['queries_using_later_timestamp'],
        real_past_price_changes=result['real_price_prefix']['past_query_price_changes'],decision=result['decision']),indent=2))

if __name__=='__main__':main()
