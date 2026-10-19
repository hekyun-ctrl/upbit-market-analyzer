"""Independent 5m research model. Never imported by the production monitor.

Three predeclared hypotheses, structural targets, next *full* bar opens after
logged publication, conservative intrabar order, gaps censored. This is NOT a
replay of live minute-level entries or an out-of-sample profitability proof.
"""
import argparse
import hashlib
import json
import math
import statistics
from collections import Counter
from datetime import datetime,timezone,timedelta
from pathlib import Path

from lbank_trend_runner import atr_wilder

VERSION='strategy_rebuild_research_v1'
NAMES=('hour_trend_vwap_reclaim','range_failed_break','volume_compression_break')
KST=timezone(timedelta(hours=9))
POLICY=dict(minimum_forward_days=30,minimum_closed_forward_trades=100,
            minimum_net_profit_factor=1.2,minimum_tp1_wilson_lower=.5,
            maximum_urgent_within_5m_fraction=.1,minimum_signals_per_day=1.,
            minimum_active_day_fraction=.5,maximum_drawdown_r=10.,
            required_cost_stress_multiple=2.,quote_latency_validation_required=True)


def ts(value):
    return datetime.fromisoformat(value.replace('Z','+00:00')).timestamp()


def segments(rows):
    seen={}
    for row in rows:
        b=row['bar'];start=b['start']
        if (start%300 or b.get('period')!=300 or b.get('quality')!='stream_closed'
                or not all(math.isfinite(b[k]) for k in ('open','high','low','close','volume'))
                or min(b[k] for k in ('open','high','low','close'))<=0 or b['volume']<0
                or b['high']<max(b['open'],b['close'],b['low'])
                or b['low']>min(b['open'],b['close'],b['high'])
                or ts(row['available_at'])<start+300):raise ValueError('invalid native record')
        if start in seen and seen[start]['bar']!=b:raise ValueError('conflicting native bars')
        if start not in seen or ts(row['available_at'])<ts(seen[start]['available_at']):seen[start]=row
    result=[]
    for row in sorted(seen.values(),key=lambda r:r['bar']['start']):
        if not result or row['bar']['start']!=result[-1][-1]['bar']['start']+300:result.append([])
        result[-1].append(row)
    return result


def hourly_direction(bars):
    by={b['start']:b for b in bars};end=(bars[-1]['start']+300)//3600*3600
    closes=[]
    for start in range(end-3*3600,end,3600):
        if any(start+t not in by for t in range(0,3600,300)):return 0
        closes.append(by[start+3300]['close'])
    if all(b>a for a,b in zip(closes,closes[1:])):return 1
    if all(b<a for a,b in zip(closes,closes[1:])):return -1
    return 0


def hypotheses(rows):
    """Rows are the observed prefix only; output geometry known at publication."""
    bars=[r['bar'] for r in rows]
    if len(bars)<22:return []
    last=bars[-1];prior=bars[-21:-1];atr=atr_wilder(bars[-250:])
    if not atr or atr<=0:return []
    volume=sum(b['volume'] for b in prior)
    if volume<=0:return []
    vwap=sum((b['high']+b['low']+b['close'])/3*b['volume'] for b in prior)/volume
    med=statistics.median(b['volume'] for b in prior);rv=last['volume']/med if med>0 else 0
    box=bars[-13:-1];hi=max(b['high'] for b in box);lo=min(b['low'] for b in box);width=hi-lo
    if width<=0:return []
    d=hourly_direction(bars);events=[]
    def add(name,side,anchor,target):
        events.append(dict(strategy=name,direction=side,anchor=anchor,target=target,
                           available_at=ts(rows[-1]['available_at']),bar_start=last['start'],
                           atr=atr,rolling_20bar_typical_price_vwap=vwap,relative_volume=rv))
    # Trend hypothesis uses the last THREE completed aligned hours, not a label
    # inferred from subsequent price. Rolling VWAP is not a daily session VWAP.
    if d and rv>=1.:
        touched=last['low']<=vwap<=last['high']
        if touched and d*(last['close']-vwap)>0 and d*(last['close']-last['open'])>0:
            target=max(b['high'] for b in prior) if d==1 else min(b['low'] for b in prior)
            add(NAMES[0],d,(last['low']-.25*atr if d==1 else last['high']+.25*atr),target)
    # Balanced range: failed sweep followed by a completed close back inside.
    balanced=abs(box[-1]['close']-box[0]['close'])<=.35*width
    if balanced:
        for side,level,key in ((1,lo,'low'),(-1,hi,'high')):
            if (side*(last[key]-level)<-.1*atr and side*(last['close']-level)>0
                    and side*(last['close']-last['open'])>0):
                add(NAMES[1],side,last[key]-side*.25*atr,(hi+lo)/2)
    # Compression plus volume is a different hypothesis, not an added veto on
    # either of the other strategies. No fitted parameter search is performed.
    if width<=3*atr and rv>=1.5:
        for side,level,key in ((1,hi,'low'),(-1,lo,'high')):
            if side*(last['close']-level)>.1*atr:
                add(NAMES[2],side,last[key]-side*.25*atr,level+side*width)
    return events


def net_unit(d,entry,exit_price,multiple=1.):
    fee,slip,funding=.0006*multiple,.0002*multiple,.0001*multiple
    effective_entry=entry*(1+d*slip)
    effective_exit=exit_price*(1-d*slip)
    return d*(effective_exit-effective_entry)-fee*(effective_entry+effective_exit)-entry*funding


def plan(event,entry):
    d=event['direction'];stop=event['anchor'];target=event['target'];atr=event['atr']
    gap=d*(entry-stop)
    if gap<=0:return None,'entry_already_invalid'
    gap=max(gap,1.5*atr,entry*.0005);stop=entry-d*gap
    if gap/entry>.006:return None,'stop_cap'
    if abs(entry-event['rolling_20bar_typical_price_vwap'])>2.5*atr:return None,'extended'
    if d*(target-entry)<=0:return None,'target_already_passed'
    if net_unit(d,entry,target)<=0:return None,'target_has_no_modeled_net_room'
    risk=-net_unit(d,entry,stop)
    if risk<=0:return None,'invalid_risk'
    return dict(event,entry=entry,stop=stop,unit_net_risk=risk),None


def trade(p,rows,index):
    """Full exit at first structural target; all strategies share this model.

    The first bar opens strictly after the signal's publication. If stop and
    target touch in one candle, stop wins; open gaps use adverse open prices.
    No target improvement, fake minute order, or entry-bar lookback exists.
    """
    d=p['direction'];stop=p['stop'];target=p['target'];entry=p['entry']
    mfe=mae=0.
    for j in range(index,min(len(rows),index+24)):
        b=rows[j]['bar'];open_price=b['open']
        if d*(open_price-stop)<=0:exit_price,reason=open_price,'gap_stop'
        elif d*(open_price-target)>=0:exit_price,reason=target,'target'
        else:
            stop_hit=d*(b['low' if d==1 else 'high']-stop)<=0
            target_hit=d*(b['high' if d==1 else 'low']-target)>=0
            if stop_hit:exit_price,reason=stop,'ambiguous_stop_first' if target_hit else 'stop'
            elif target_hit:exit_price,reason=target,'target'
            else:
                mfe=max(mfe,d*(b['high' if d==1 else 'low']-entry));mae=max(mae,-d*(b['low' if d==1 else 'high']-entry))
                continue
        # A touching candle's full extremes may occur after exit; do not use
        # them for an optimistic MFE/MAE claim.
        return dict(reason=reason,closed_at=b['start']+300,exit=exit_price,
                    r=net_unit(d,entry,exit_price)/p['unit_net_risk'],
                    stressed_r=net_unit(d,entry,exit_price,2.)/p['unit_net_risk'],
                    tp1=reason=='target',mfe_lower_bound_price=mfe,mae_lower_bound_price=mae)
    if index+24<=len(rows):
        b=rows[index+23]['bar'];exit_price=b['close']
        return dict(reason='time_exit',closed_at=b['start']+300,exit=exit_price,
                    r=net_unit(d,entry,exit_price)/p['unit_net_risk'],
                    stressed_r=net_unit(d,entry,exit_price,2.)/p['unit_net_risk'],tp1=False,
                    mfe_lower_bound_price=mfe,mae_lower_bound_price=mae)
    return dict(reason='censored_gap_or_history_end',closed_at=rows[-1]['bar']['start']+300,
                r=None,stressed_r=None,tp1=None)


def wilson_lower(wins,n):
    if not n:return None
    z=1.96;p=wins/n
    return (p+z*z/(2*n)-z*math.sqrt(p*(1-p)/n+z*z/(4*n*n)))/(1+z*z/n)


def summarize(trades):
    closed=[r for r in trades if r['r'] is not None];values=[r['r'] for r in closed]
    gains=sum(max(v,0) for v in values);losses=-sum(min(v,0) for v in values)
    equity=peak=drawdown=0.
    for v in values:equity+=v;peak=max(peak,equity);drawdown=max(drawdown,peak-equity)
    wins=sum(r['tp1'] for r in closed)
    return dict(trades=len(trades),closed=len(closed),censored=len(trades)-len(closed),
                first_target_count=wins,first_target_fraction=wins/len(closed) if closed else None,
                first_target_wilson_lower=wilson_lower(wins,len(closed)),
                net_expectancy_r=statistics.mean(values) if values else None,
                net_total_r=sum(values),profit_factor=gains/losses if losses else None,
                stress_2x_expectancy_r=statistics.mean(r['stressed_r'] for r in closed) if closed else None,
                max_drawdown_r=drawdown,actual_pnl=False)


def promotion_gate(metrics):
    """Fail closed on absent evidence; development replay can never qualify."""
    failures=[]
    checks=[(metrics.get('untouched_forward',False),'not_untouched_forward'),
        (metrics.get('forward_days',0)>=POLICY['minimum_forward_days'],'insufficient_forward_days'),
        (metrics.get('closed',0)>=POLICY['minimum_closed_forward_trades'],'insufficient_forward_trades'),
        (metrics.get('net_mean_lower_95') is not None and metrics['net_mean_lower_95']>0,'net_expectancy_not_established'),
        (metrics.get('profit_factor') is not None and metrics['profit_factor']>=POLICY['minimum_net_profit_factor'],'profit_factor'),
        (metrics.get('first_target_wilson_lower') is not None and metrics['first_target_wilson_lower']>POLICY['minimum_tp1_wilson_lower'],'first_target_reliability'),
        (metrics.get('urgent_5m_fraction') is not None and metrics['urgent_5m_fraction']<=POLICY['maximum_urgent_within_5m_fraction'],'early_urgent_risk_not_validated'),
        (metrics.get('signals_per_day',0)>=POLICY['minimum_signals_per_day'],'insufficient_frequency'),
        (metrics.get('active_day_fraction',0)>=POLICY['minimum_active_day_fraction'],'insufficient_active_days'),
        (metrics.get('stress_2x_expectancy_r') is not None and metrics['stress_2x_expectancy_r']>0,'cost_stress'),
        (metrics.get('max_drawdown_r') is not None and metrics['max_drawdown_r']<=POLICY['maximum_drawdown_r'],'drawdown'),
        (metrics.get('quote_latency_validated',False),'execution_not_validated')]
    failures=[reason for ok,reason in checks if not ok]
    return dict(eligible=not failures,failures=failures)


def alert_audit(data):
    references={81639.8:8,82090.3:9,82469.1:10,82568.1:11,82466.5:12}
    import re
    follow={int(re.search(r'reference=(\d+)',r['message'])[1]):r for r in data['followups']}
    result=[]
    for row in data['entries']:
        m=re.search(r'side=(\S+) entry=([\d.]+) stop=([\d.]+) t1=([\d.]+)',row['message'])
        side,entry,stop,target=m.groups();entry,stop,target=map(float,(entry,stop,target));ref=references[entry]
        d=1 if side=='롱' else -1;start=ts(row['at']);f=follow[ref];end=ts(f['at'])
        touched=[];coverage=[]
        for record in data['bars']:
            b=record['bar']
            if b['start']+300<=start or b['start']>=end:continue
            coverage.append(b)
            extreme=b['high'] if d==1 else b['low']
            if d*(extreme-target)>=0:touched.append(b)
        complete_coverage=(bool(coverage) and coverage[0]['start']<=start and coverage[-1]['start']+300>=end
                           and all(b['start']==a['start']+300 for a,b in zip(coverage,coverage[1:])))
        result.append(dict(reference=ref,entry_at=row['at'],followup_at=f['at'],
                           followup_reason=re.search(r'reason=(\w+)',f['message'])[1],
                           first_target_before_followup=False if complete_coverage and not touched else None,
                           possible_target_touch_bars=len(touched),complete_5m_coverage=complete_coverage,
                           target_distance_pct=d*(target/entry-1)*100,actual_fill_verified=False))
    return dict(cases=result,entries=len(result),risk_followups=sum(c['followup_reason'] in ('early_invalidation','stop_breached') for c in result),
                time_followups=sum(c['followup_reason']=='time_exit' for c in result),
                target_before_followup_confirmed_absent=sum(c['first_target_before_followup'] is False for c in result),
                unknown=sum(c['first_target_before_followup'] is None for c in result),
                scope='Five original signals, old differing policy versions, not user fills or new-policy performance.')


def run(path):
    path=Path(path);raw=path.read_bytes();data=json.loads(raw);parts=segments(data['bars'])
    counts=Counter();rejections={name:Counter() for name in NAMES};trades={name:[] for name in NAMES}
    blocked={name:0 for name in NAMES};day_counts={name:Counter() for name in NAMES}
    for rows in parts:
        by={r['bar']['start']:i for i,r in enumerate(rows)}
        for n in range(22,len(rows)+1):
            prefix=rows[:n]
            # If a historical candle's publication is delayed beyond the
            # decision, do not pretend it was part of the observed prefix.
            if any(ts(r['available_at'])>ts(prefix[-1]['available_at']) for r in prefix):continue
            for event in hypotheses(prefix):
                name=event['strategy'];counts[name]+=1
                if event['available_at']<blocked[name]:rejections[name]['position_or_cooldown']+=1;continue
                entry_start=math.ceil(event['available_at']/300)*300
                index=by.get(entry_start)
                if index is None:rejections[name]['entry_bar_unavailable']+=1;continue
                day=datetime.fromtimestamp(entry_start,KST).date().isoformat()
                if day_counts[name][day]>=8:rejections[name]['daily_cap']+=1;continue
                p,reason=plan(event,rows[index]['bar']['open'])
                if p is None:rejections[name][reason]+=1;continue
                outcome=trade(p,rows,index);trades[name].append(dict(plan=p,entry_at=entry_start,**outcome))
                day_counts[name][day]+=1;blocked[name]=max(outcome['closed_at'],entry_start+2700)
    summary={name:summarize(trades[name]) for name in NAMES}
    return dict(version=VERSION,dataset_sha256=hashlib.sha256(raw).hexdigest(),
        bars=sum(len(s) for s in parts),observed_hours=sum(len(s) for s in parts)/12,
        span_days=(parts[-1][-1]['bar']['start']+300-parts[0][0]['bar']['start'])/86400,
        segments=[dict(bars=len(s),start=s[0]['bar']['start'],end=s[-1]['bar']['start']+300) for s in parts],
        actual_alert_audit=alert_audit(data),setups={n:counts[n] for n in NAMES},
        rejections={n:dict(rejections[n]) for n in NAMES},summary=summary,trades=trades,
        promotion={n:promotion_gate(summary[n]) for n in NAMES},promotion_policy=POLICY,
        production_monitor_changed=False,live_strategy_profitability_comparison_available=False,
        untouched_out_of_sample=False,actual_pnl=False,parameter_search_performed=False,
        limitations=['Development dataset already inspected in earlier incident reviews; not untouched holdout.',
        'Only 5m bars, entry at the first complete bar open after publication. This deliberately coarse delay is not a proposed live execution delay.',
        'All three alternatives use one full structural-target exit; not the live half-TP1/runner policy.',
        'Original full minute/tick history, actual fills and urgent-warning events for these alternatives are unavailable.',
        'Candle stop/target conflict uses stop first; gaps/end-of-history censor incomplete trades.',
        'No live-strategy replica, no profitable alternative selected, no promotion or frequency improvement established.'])


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--input',required=True);parser.add_argument('--output',required=True)
    args=parser.parse_args();result=run(args.input);Path(args.output).write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n')
    print(json.dumps({k:v for k,v in result.items() if k not in ('trades',)},ensure_ascii=False,indent=2))
