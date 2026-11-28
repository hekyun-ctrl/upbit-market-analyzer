"""Chronological detection audit on actual native 5m bars; NOT a backtest.

No synthetic minute candles, entry fills or returns are constructed. Complete
native minute history and later quotes are required by the prospective study.
"""
import json
import sys
from collections import Counter
from datetime import datetime,timezone
from pathlib import Path

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from lbank_public_techniques import analyze_bars,LABELS,VERSION


def audit():
    folder=Path(__file__).parent
    sources=['native-5m-audit-20261017.json','rsi-divergence-native-20261018.json','four-traders-native-20261019.json']
    by_start={};conflicts=[]
    for name in sources:
        for bar in json.loads((folder/name).read_text())['bars']:
            if bar['start'] in by_start:
                for key in ('open','high','low','close','volume'):
                    if abs(bar[key]-by_start[bar['start']][key])>1e-8:conflicts.append(bar['start'])
            by_start[bar['start']]=bar
    if conflicts:raise ValueError('conflicting native records')
    segments=[]
    for bar in sorted(by_start.values(),key=lambda b:b['start']):
        if not segments or bar['start']-segments[-1][-1]['start']!=300:segments.append([])
        segments[-1].append(bar)
    unique={};ready=hour_ready=0
    for segment in segments:
        for n in range(22,len(segment)+1):
            prefix=segment[max(0,n-300):n];now=prefix[-1]['start']+300
            ctx=analyze_bars(prefix,now)
            ready+=int(ctx['ready']);hour_ready+=int(ctx.get('hourly_ready',False))
            for event in ctx['setups']:
                assert event['available_at']<=now<=event['expires_at']
                if event['id'] in unique:assert event==unique[event['id']]
                unique[event['id']]=event
    counts=Counter(e['strategy'] for e in unique.values())
    stamp=lambda t:datetime.fromtimestamp(t,timezone.utc).isoformat()
    return dict(version=VERSION,sources=sources,bar_count=len(by_start),
        observed_hours=len(by_start)*5/60,segments=[dict(bars=len(s),start_utc=stamp(s[0]['start']),end_utc=stamp(s[-1]['start']+300)) for s in segments],
        ready_bar_evaluations=ready,hourly_ready_evaluations=hour_ready,
        unique_setups={name:counts[name] for name in LABELS},events=list(unique.values()),
        lookahead_used=False,synthetic_minutes_used=False,entry_count_verified=False,
        profitability_verified=False,live_frequency_effect_verified=False,
        limitation='Setup counts only. Missing original full minute history prevents replay of minute confirmation, recheck, risk, cooldown and actual alert delivery.')


if __name__=='__main__':
    result=audit();path=Path(__file__).with_name('public-techniques-audit-20261019.json')
    path.write_text(json.dumps(result,ensure_ascii=False,indent=2))
    print(json.dumps({k:v for k,v in result.items() if k!='events'},ensure_ascii=False,indent=2))
