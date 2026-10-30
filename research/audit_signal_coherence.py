"""Price-path audit of user screenshots; not a strategy backtest or fill proof."""
import json
from pathlib import Path
from datetime import datetime,timezone,timedelta

KST=timezone(timedelta(hours=9))


def stamp(t):return datetime.fromtimestamp(t,KST).isoformat()


def audit():
    data=json.loads(Path(__file__).with_name('signal-coherence-native-20261019.json').read_text())
    by_start={}
    for b in data['bars']:
        if b['start'] in by_start:
            assert all(b[k]==by_start[b['start']][k] for k in ('open','high','low','close','volume'))
        assert b['quality']=='stream_closed'
        by_start[b['start']]=b
    bars=sorted(by_start.values(),key=lambda b:b['start'])
    cases=[]
    for c in data['cases']:
        # A first partial candle cannot prove when a touch occurred within it.
        full=[b for b in bars if c['time']<=b['start']<c['time']+21600]
        partial=by_start.get(int(c['time']//300)*300)
        first_target=next((b for b in full if b['high']>=c['t1']),None)
        until_target=[b for b in full if first_target and b['start']<=first_target['start']]
        covered=([partial] if partial else [])+until_target
        contiguous=bool(first_target and partial and covered and all(b['start']-a['start']==300 for a,b in zip(covered,covered[1:])))
        same_bar_conflict=bool(first_target and first_target['low']<=c['stop'])
        cases.append(dict(reference=c['id'],entry_kst=stamp(c['time']),entry=c['entry'],stop=c['stop'],
            target1=c['t1'],full_post_entry_bars=len(full),
            observed_min_low=min(b['low'] for b in full),observed_max_high=max(b['high'] for b in full),
            first_observed_target1_interval_kst=([stamp(first_target['start']),stamp(first_target['start']+300)] if first_target else None),
            stop_touch_in_observed_full_bars=any(b['low']<=c['stop'] for b in full),
            continuous_entry_through_target1=contiguous,
            no_observed_stop_before_target1=(contiguous and not same_bar_conflict and all(b['low']>c['stop'] for b in covered)),
            partial_entry_candle_not_used_as_post_entry_touch=True,
            intrabar_order_or_venue_fill_verified=False,
            gaps=[dict(after_kst=stamp(a['start']+300),before_kst=stamp(b['start']))
                  for a,b in zip(full,full[1:]) if b['start']-a['start']!=300]))
    return dict(source=data['source'],unique_bars=len(bars),cases=cases,
        old_warning_prices={'8':81530.9,'10':82325.5},
        signal9_six_hour_price=82637.0,
        strategy_profitability_verified=False,
        limitations=['Screenshot/reference values are rounded.',
                    'No original full minute history, venue positions, fills, or Mark Price trigger history.',
                    'No missing candle was filled. A gap prevents a claim about unobserved extrema.',
                    'No retroactive new-policy win rate or PnL is asserted.'])


if __name__=='__main__':
    result=audit()
    Path(__file__).with_name('signal-coherence-audit-20261019.json').write_text(json.dumps(result,ensure_ascii=False,indent=2))
    print(json.dumps(result,ensure_ascii=False,indent=2))
