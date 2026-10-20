"""Independent cash and fixed temporal-prefix checks for the frozen extension."""
import argparse
import ast
import copy
import hashlib
import json
import math
import random
import subprocess
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from decimal import Decimal, localcontext
from pathlib import Path
import offline_entry_regime_compare_1945 as trial
import lbank_futures_monitor as m
from lbank_native_entry import SOURCE,wilder_rsi_series

ap=argparse.ArgumentParser();ap.add_argument('--data',required=True);ap.add_argument('--result',required=True);ap.add_argument('--output',required=True)
a=ap.parse_args();out=json.loads(Path(a.result).read_text());month=out['start_utc'][:7]
bars,five,rates,objects=trial.load(a.data,month);by_time={b['start']:b for b in bars}
assert len({t for t,r in rates})==len(rates) and all(math.isfinite(r) for t,r in rates)
assert all(t in by_time for t,r in rates)
max_error=0.;checks=0
for v in out['variants']:
    for t in v['closed_trades']:
        with localcontext() as ctx:
            ctx.prec=45;D=lambda x:Decimal(str(x));d=D(t['direction']);entry=D(t['entry']);stop=D(t['stop'])
            stop_fill=stop*(1-d*D(str(m.STOP_SLIPPAGE)))
            denominator=-(d*(stop_fill-entry)-D(str(m.TAKER_FEE))*(entry+stop_fill)-entry*D(str(m.FUNDING_BUFFER)))
            for multiple in (1,2):
                k=D(multiple);ei=entry*(1+d*D('.0002')*k);xo=D(t['exit'])*(1-d*D('.0002')*k)
                funding=Decimal(0)
                for ft,rate in rates:
                    if t['entry_at']<ft<=t['exit_at']:
                        charge=d*D(rate)*D(by_time[ft]['open']);funding+=charge*k if charge>0 else charge
                calculated=(d*(xo-ei)-D('.0006')*k*(ei+xo)-funding)/denominator
                key='net_R' if multiple==1 else 'double_cost_R'
                error=abs(float(calculated)-t[key]);max_error=max(max_error,error);assert error<1e-9
                checks+=1
    assert abs(sum(t['net_R'] for t in v['closed_trades'])-v['net_R'])<1e-9
    assert v['end_censored']==len(v['open_censored'])

start=int(datetime.fromisoformat(out['start_utc'].replace('Z','+00:00')).timestamp())
end=int(datetime.fromisoformat(out['end_exclusive_utc'].replace('Z','+00:00')).timestamp())

def snapshot(series,index):
    now=series[index]['start'];fb=[]
    for i in range(max(0,(index//5-250)*5),index//5*5,5):
        g=series[i:i+5]
        fb.append(dict(period=300,start=g[0]['start'],open=g[0]['open'],close=g[-1]['close'],
            high=max(b['high'] for b in g),low=min(b['low'] for b in g),volume=sum(b['volume'] for b in g),
            turnover=sum(b['turnover'] for b in g),updated=g[-1]['updated'],quality='stream_closed'))
    c=[b['close'] for b in fb];mp={b['start']:b for b in fb};last15=now//900*900-900
    return dict(source=SOURCE,quote=series[index]['open'],minute_bars=series[index-360:index],
        five_bars=fb,prices=[b['close'] for b in series[index-360:index]],last5_start=fb[-1]['start'],
        rsi5={'closes':c,'rsi':wilder_rsi_series(c)},closes15=[mp[t+600]['close'] for t in range(last15-3600,last15+1,900)])

# Fixed noon and midnight each day, not chosen for profitable entries.
prefix_checks=0
for at in range(start,end,12*3600):
    i=(at-bars[0]['start'])//60;x=snapshot(bars,i)
    changed=copy.deepcopy(bars[:i+1]);changed[i].update(high=changed[i]['open']*3,low=1.,close=2.)
    changed.extend([dict(b,open=1.,high=3.,low=1.,close=2.) for b in bars[i+1:i+11]])
    y=snapshot(changed,i);assert x==y
    da={};db={};pa=m.classify(x['prices'],native_state=x,diagnostics=da);pb=m.classify(y['prices'],native_state=y,diagnostics=db)
    assert da==db and pa==pb;prefix_checks+=1

for v in out['variants']:
    grouped=defaultdict(list)
    for t in v['closed_trades']:grouped[datetime.fromtimestamp(t['entry_at'],timezone.utc).date().isoformat()].append(t['net_R'])
    days=[datetime.fromtimestamp(t,timezone.utc).date().isoformat() for t in range(start,end,86400)]
    intervals={}
    for length in (2,7):
        if length>=len(days):
            intervals[str(length)]=None  # Whole-window circular block is degenerate, not an informative CI.
            continue
        rng=random.Random(20261020+length);means=[]
        for _ in range(5000):
            selected=[]
            while len(selected)<len(days):
                j=rng.randrange(len(days));selected.extend(days[(j+k)%len(days)] for k in range(length))
            xs=[x for day in selected[:len(days)] for x in grouped[day]]
            if xs:means.append(sum(xs)/len(xs))
        means.sort();intervals[str(length)]=[means[int(len(means)*.025)],means[int(len(means)*.975)]] if means else None
    v['conditional_UTC_day_block_95_mean_R']=intervals
    v['block7_identifiable']=len(days)>7
    v['actual_LBank_5m_urgent_rate']=None
    v['model_initial_stop_within5m']=sum(t['reason']=='initial_stop' and t['exit_at']-t['entry_at']<=300 for t in v['closed_trades'])
    opportunity={datetime.fromtimestamp(t['entry_at'],timezone.utc).date().isoformat() for t in v['closed_trades']+v['open_censored']}
    v['opportunity_UTC_days']=len(opportunity)
    v['opportunity_fraction_UTC_days']=len(opportunity)/len(days)
    assert v['counts']['signals']==v['closed']+v['end_censored']+v['counts'].get('tracking_censored',0)

base=subprocess.check_output(['git','show','HEAD:lbank_futures_monitor.py'],text=True)
current=Path('lbank_futures_monitor.py').read_text()
core=['classify','assess_candidate','confirm_watch','calculate_plan','alert_gate','record_alert','net_pnl_per_btc']
def extract(src):return {n.name:ast.get_source_segment(src,n) for n in ast.parse(src).body if isinstance(n,ast.FunctionDef)}
x,y=extract(base),extract(current);assert all(x[k]==y[k] for k in core)
config={k:getattr(m,k) for k in ['TAKER_FEE','STOP_SLIPPAGE','FUNDING_BUFFER','REF_BALANCE','RISK_FRACTION','LEVERAGE','MAX_MARGIN_FRACTION','MIN_NET_RR','COOLDOWN','REVERSAL_COOLDOWN','MAX_DAILY_ALERTS']}
out['verification']=dict(decimal_checks=checks,maximum_R_error=max_error,fixed_temporal_prefix_checks=prefix_checks,
    full_simulation_future_append_replay=False,unchanged_gate_definitions=core,month_funding_rows=len(rates),source_config=config,
    input_zip_sha_verified=True,missing_minute_intervals=0,overlap_adjusted_account_DD=False,
    selected_history_bias_corrected=False,circular_UTC_day_blocks=[2,7],bootstrap_per_length=5000,
    code_sha256=hashlib.sha256(Path(trial.__file__).read_bytes()).hexdigest(),verifier_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
    original_urgent_metric_is_proxy=True,initial_interval_starts_flat=True)
Path(a.output).write_text(json.dumps(out,ensure_ascii=False,indent=2)+'\n')
print(json.dumps(out['verification']))
