"""Independent decimal cash, input causality and unchanged-gate checks."""
import argparse, ast, copy, hashlib, json, math, random, subprocess
from collections import defaultdict
from datetime import datetime, timezone, timedelta
from decimal import Decimal, localcontext
from pathlib import Path
import offline_current_entry_compare as trial
import lbank_futures_monitor as m
from lbank_native_entry import SOURCE, wilder_rsi_series

ap=argparse.ArgumentParser()
ap.add_argument('--data',required=True)
ap.add_argument('--result',required=True)
ap.add_argument('--output',required=True)
args=ap.parse_args()
out=json.loads(Path(args.result).read_text())
bars,five,rates,objects=trial.load(args.data)
by_start={b['start']:b for b in bars}
maxerr=0.;checks=0
for variant in out['variants']:
    for t in variant['closed_trades']:
        with localcontext() as ctx:
            ctx.prec=45
            D=lambda x:Decimal(str(x));d=D(t['direction']);entry=D(t['entry']);stop=D(t['stop'])
            stop_fill=stop*(1-d*D('.0002'))
            denominator=-(d*(stop_fill-entry)-D('.0006')*(entry+stop_fill)-entry*D('.0001'))
            for multiple in (1,2):
                k=D(multiple);ei=entry*(1+d*D('.0002')*k);xo=D(t['exit'])*(1-d*D('.0002')*k)
                funding=Decimal(0)
                for ft,rate in rates:
                    if t['entry_at']<ft<=t['exit_at']:
                        charge=d*D(rate)*D(by_start[ft]['open'])
                        funding+=charge*k if charge>0 else charge
                cash=d*(xo-ei)-D('.0006')*k*(ei+xo)-funding
                key='net_R' if multiple==1 else 'double_cost_R'
                err=abs(float(cash/denominator)-t[key]);maxerr=max(err,maxerr)
                assert err<1e-9
                checks+=1
    assert abs(sum(t['net_R'] for t in variant['closed_trades'])-variant['net_R'])<1e-10

def native(series,index):
    # Rebuild at one historical boundary independently; never include that minute's H/L/C.
    now=series[index]['start'];finished=series[:index];five=[]
    for i in range(max(0,(index//5-250)*5),index//5*5,5):
        g=series[i:i+5]
        five.append(dict(period=300,start=g[0]['start'],open=g[0]['open'],close=g[-1]['close'],
            high=max(b['high'] for b in g),low=min(b['low'] for b in g),
            volume=sum(b['volume'] for b in g),turnover=sum(b['turnover'] for b in g),
            updated=g[-1]['updated'],quality='stream_closed'))
    closes=[b['close'] for b in five];mp={b['start']:b for b in five};final=now//900*900-900
    return dict(source=SOURCE,quote=series[index]['open'],minute_bars=finished[-360:],five_bars=five,
        prices=[b['close'] for b in finished[-360:]],last5_start=five[-1]['start'],
        closes15=[mp[t+600]['close'] for t in range(final-3600,final+1,900)],
        rsi5={'closes':closes,'rsi':wilder_rsi_series(closes)})

# Retain current known open; change current H/L/C and every future candle.
points=sorted({t['entry_at'] for v in out['variants'] for t in v['closed_trades']})
prefix_checks=0
for at in points:
    i=(at-bars[0]['start'])//60;before=native(bars,i)
    future=copy.deepcopy(bars[:i+1]);future[i].update(high=future[i]['open']*3,low=1.,close=2.)
    future.extend([dict(b,start=b['start'],open=1.,high=3.,low=1.,close=2.) for b in bars[i+1:i+11]])
    after=native(future,i)
    assert before==after
    a={};b={};pa=m.classify(before['prices'],native_state=before,diagnostics=a)
    pb=m.classify(after['prices'],native_state=after,diagnostics=b)
    assert a==b and pa==pb
    prefix_checks+=1

baseline=subprocess.check_output(['git','show','HEAD:lbank_futures_monitor.py'],text=True)
patched=Path('lbank_futures_monitor.py').read_text()
core=['classify','assess_candidate','confirm_watch','calculate_plan','alert_gate','record_alert','net_pnl_per_btc']
def defs(src):return {n.name:ast.dump(n,include_attributes=False) for n in ast.parse(src).body if isinstance(n,ast.FunctionDef)}
a,b=defs(baseline),defs(patched)
assert all(a[k]==b[k] for k in core)

for variant in out['variants']:
    grouped=defaultdict(list)
    for t in variant['closed_trades']:
        day=datetime.fromtimestamp(t['entry_at'],timezone(timedelta(hours=9))).date().isoformat()
        grouped[day].append(t['net_R'])
    days=[(datetime(2024,1,8)+timedelta(days=i)).date().isoformat() for i in range(8)]
    rng=random.Random(20261020);means=[]
    for _ in range(5000):
        selected=[]
        while len(selected)<8:
            j=rng.randrange(8);selected.extend([days[j],days[(j+1)%8]])
        xs=[x for day in selected[:8] for x in grouped[day]]
        if xs:means.append(sum(xs)/len(xs))
    means.sort()
    variant['conditional_2day_circular_block95_mean_R']=[means[int(len(means)*.025)],means[int(len(means)*.975)]]
    variant['model_initial_stop_within5m']=sum(t['reason']=='initial_stop' and t['exit_at']-t['entry_at']<=300 for t in variant['closed_trades'])
    variant['actual_LBank_5m_urgent_rate']=None
    variant['end_censored_not_liquidated']=True
out['verification']=dict(decimal_closed_variant_rows=sum(len(v['closed_trades']) for v in out['variants']),
    decimal_cost_checks=checks,maximum_abs_R_error=maxerr,independent_signal_prefix_comparisons=prefix_checks,
    unchanged_core_gate_definitions=core,block_samples=5000,block_unit='2KST calendar days circular;8dates include partial boundary days',
    selection_bias_corrected=False,full_simulation_future_append_replay=False,
    original_urgent_metric_is_diagnostic_not_actual_LBank_rate=True)
Path(args.output).write_text(json.dumps(out,ensure_ascii=False,indent=2)+'\n')
print(json.dumps(out['verification']))
