"""Rank observable evidence without adding a universal confirmation wait.

Hard risk/data/cost constraints remain outside ranking. Larger native structure
can outweigh a small minute reaction; no score is a win probability.
"""
import math
from lbank_native_entry import SOURCE
from lbank_trend_runner import valid_state, ema_series

POLICY = "native_structure_priority_v2"


def observed_target_room(bars, direction, price, target):
    """Use confirmed native pivots; unknown room is never labeled clear.

    This is shared by structural ranking and the monetary plan gate so a
    known obstacle cannot merely disable a ranking bonus and then be ignored.
    The caller supplies the already validated, closed native candle snapshot.
    """
    unknown = dict(policy="confirmed_native_pivot_room_v1",
                   target_room="unknown", observed_barrier=None)
    if direction not in (-1, 1) or not bars:
        return unknown
    d = direction
    key = 'high' if d == 1 else 'low'
    obstacles = []
    for i in range(2, len(bars)-2):
        v = bars[i][key]
        if (all(d*(v-bars[j][key]) > 0 for j in (i-2, i-1, i+1, i+2))
                and d*(v-price) > 0):
            obstacles.append(v)
    barrier = (min(obstacles) if d == 1 else max(obstacles)) if obstacles else None
    room = ('unknown' if barrier is None or target is None else
            'clear' if d*(barrier-target) >= price*.00015 else 'conflict')
    return dict(unknown, target_room=room, observed_barrier=barrier)


def direction_priority(state, direction, price, target, now):
    """Unknown or stale data never grants dominant structural support."""
    unknown = dict(policy=POLICY, dominant_intact=False, ready=False,
                   order=["hard_risk", "higher_structure", "target_room", "minute_reaction"])
    try:
        if (direction not in (-1,1) or not math.isfinite(price) or price<=0
                or not valid_state(state,now)):
            return unknown
        bars=state['five_bars']; last=bars[-3:]; d=direction
        swings=all(d*(b['high']-a['high'])>0 and d*(b['low']-a['low'])>0
                   for a,b in zip(last,last[1:]))
        support=min(b['low'] for b in last) if d==1 else max(b['high'] for b in last)
        intact=d*(price-support)>0
        c15=state['closes15']
        trend15=len(c15)>=3 and all(d*(b-a)>0 for a,b in zip(c15[-3:],c15[-2:]))
        closes=[b['close'] for b in bars]
        fast,slow=ema_series(closes,9),ema_series(closes,21)
        alignment=d*(fast[-1]-slow[-1])>0 and d*(fast[-1]-fast[-2])>0
        target_evidence = observed_target_room(bars, d, price, target)
        barrier, room = target_evidence['observed_barrier'], target_evidence['target_room']
        return dict(unknown,ready=True,bar_start=bars[-1]['start'],
                    swing_structure=swings,support=support,support_intact=intact,
                    trend15_aligned=trend15,ema_aligned=alignment,target_room=room,
                    observed_barrier=barrier,
                    dominant_intact=bool(swings and intact and trend15 and alignment and room!='conflict'))
    except (KeyError,TypeError,ValueError,IndexError,OverflowError):
        return unknown


def confirmation_quality(state,watch):
    """Hard candidate invalidation only; minute reactions are supporting evidence."""
    try:
        d,price,quote=watch['direction'],watch['price'],state['quote']
        start=watch['native_watch_1m_start'];bars=state['minute_bars']
        if (state['source']!=SOURCE or d not in (-1,1) or not bars
                or not all(math.isfinite(v) and v>0 for v in (price,quote))
                or not isinstance(start,int) or start%60):
            return False,'invalid_native_confirmation_evidence',None
        after=[b for b in bars if b['start']>=start+120]
        stop=float(watch['stop'])
        if not math.isfinite(stop) or stop<=0 or d*(price-stop)<=0:
            return False,'invalid_native_confirmation_evidence',None
        for b in after:
            o,h,l,c=[float(b[k]) for k in ('open','high','low','close')]
            if (b['quality']!='stream_closed' or b['start']%60
                    or not all(math.isfinite(v) and v>0 for v in (o,h,l,c))
                    or l>min(o,c) or h<max(o,c) or l>h):
                return False,'invalid_native_confirmation_evidence',None
        if any(b['start']-a['start']!=60 for a,b in zip(after,after[1:])):
            return False,'invalid_native_confirmation_evidence',None
        key='low' if d==1 else 'high'
        if d*(quote-stop)<=0 or any(d*(b[key]-stop)<=0 for b in after):
            return False,'watch_structure_stop_crossed',None
        level=watch.get('level')
        if watch.get('strategy')=='breakout':
            if level is None or not math.isfinite(level) or level<=0:
                return False,'breakout_level_unavailable',None
            if d*(quote-level)<=0:
                return False,'breakout_level_not_held',None
        last=after[-2:]
        reaction=(len(last)==2 and all(d*(b['close']-b['open'])>0 and
                     d*(b['close']-price)>0 for b in last)
                  and d*(last[1]['close']-last[0]['close'])>0
                  and d*(quote-last[-1]['close'])>=0)
        priority=direction_priority(state,d,quote,watch.get('target1'),bars[-1]['start']+60)
        return True,'native_priority_evaluated',dict(
            policy=POLICY,watch_last_closed_1m=start,
            confirmed_1m_starts=[b['start'] for b in last],
            confirmed_1m_closes=[b['close'] for b in last],
            watch_price=price,confirmation_quote=quote,
            signed_progress_pct=d*(quote/price-1)*100,
            minute_followthrough=bool(reaction),requires_two_minute_wait=False,
            priority=priority,frozen_breakout_level=level if watch.get('strategy')=='breakout' else None)
    except (KeyError,TypeError,ValueError,IndexError,OverflowError):
        return False,'invalid_native_confirmation_evidence',None
