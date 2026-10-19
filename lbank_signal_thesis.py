"""Causal post-entry evidence. Missing support is not a confirmed reversal.

This module never qualifies an entry, changes position size, or widens a stop.
It freezes an observed structural reference, independently of rolling scores.
"""
import copy
import math

from lbank_native_entry import SOURCE
from lbank_trend_runner import valid_state, ema_series

POLICY = 'native_frozen_thesis_v3'
REVIEW_SECONDS = 6 * 3600
TRACKING_SECONDS = 24 * 3600


def attach_thesis(plan, state, now):
    """Presentation/followup only, called after every live entry gate."""
    if plan.get('data_source') != SOURCE:
        return plan
    d, entry, stop = plan['direction'], plan['price'], plan['stop']
    anchor, source = plan.get('structure_anchor'), 'setup_anchor'
    if anchor is None:
        anchor = (plan.get('entry_quality') or {}).get('priority',{}).get('support')
        source = 'entry_observed_5m_support'
    # A reference behind the hard stop cannot provide an earlier invalidation.
    if (not isinstance(anchor,(int,float)) or not math.isfinite(anchor)
            or not d*(entry-anchor)>0 or not d*(anchor-stop)>0):
        anchor, source = None, 'hard_stop_and_confirmed_reversal'
    thesis = dict(policy=POLICY, frozen_at=now, entry=entry, initial_stop=stop,
                  anchor=anchor, anchor_source=source,
                  entry_bar_start=state.get('last5_start') if state else None,
                  review_seconds=REVIEW_SECONDS, tracking_seconds=TRACKING_SECONDS)
    return dict(plan,followup_thesis=copy.deepcopy(thesis))


def _bar_valid(bar,period,now):
    try:
        o,h,l,c=[float(bar[k]) for k in ('open','high','low','close')]
        return (bar.get('quality')=='stream_closed' and bar['start']%period==0
                and bar['start']+period<=now
                and all(math.isfinite(x) and x>0 for x in (o,h,l,c))
                and h>=max(o,c,l) and l<=min(o,c,h))
    except (KeyError,TypeError,ValueError):
        return False


def observed_stop_touch(state,d,stop,active_since,now):
    """Only fully post-activation candles; a rebound cannot erase a stop touch."""
    if not state or state.get('source')!=SOURCE:
        return None
    key='low' if d==1 else 'high'
    for name,period in (('minute_bars',60),('five_bars',300)):
        for b in state.get(name,[]):
            if (b.get('start',-1)>=active_since and _bar_valid(b,period,now)
                    and d*(b[key]-stop)<=0):
                return dict(reason='observed_stop_wick',bar_start=b['start'],
                            period=period,observed_extreme=b[key],stop=stop)
    return None


def assess_thesis(thesis,state,d,price,sent_at,now):
    """Require affirmative failure; a lost AND component is just weaker evidence."""
    result=dict(policy=POLICY,status='unknown',reason='native_evidence_unavailable',
                invalidated=False,anchor=thesis.get('anchor'),evaluated_at=now)
    anchor=thesis.get('anchor')
    if (not isinstance(thesis.get('entry'),(int,float)) or not math.isfinite(thesis['entry'])
            or thesis['entry']<=0 or (anchor is not None and
            (not isinstance(anchor,(int,float)) or not math.isfinite(anchor) or anchor<=0))):
        return dict(result,reason='invalid_frozen_reference',anchor=None)
    if not valid_state(state,now):
        return result
    bars=state['five_bars'];last=bars[-1];anchor=thesis.get('anchor')
    result.update(bar_start=last['start'],last_close=last['close'])
    # The level was frozen at entry; only a fully post-entry completed bar counts.
    if (anchor is not None and last['start']>=sent_at
            and d*(last['close']-anchor)<0):
        return dict(result,status='invalid',reason='frozen_structure_close_break',invalidated=True)
    closes=[b['close'] for b in bars];fast=ema_series(closes,9);slow=ema_series(closes,21)
    c15=state['closes15']
    opposite15=(len(c15)>=3 and all(d*(b-a)<0 for a,b in zip(c15[-3:],c15[-2:])))
    opposite_ema=d*(fast[-1]-slow[-1])<0 and d*(fast[-1]-fast[-2])<0
    last3=bars[-3:]
    opposite_swings=all(d*(b['high']-a['high'])<0 and d*(b['low']-a['low'])<0
                        for a,b in zip(last3,last3[1:]))
    # Both 15m changes must be fully after entry, not a pre-entry countertrend.
    reversal_since=int(now//900)*900-2700
    reversal=(reversal_since>=sent_at and opposite15 and opposite_ema and opposite_swings
              and d*(price-thesis['entry'])<0)
    result.update(opposite15=opposite15,opposite_ema=opposite_ema,
                  opposite_swings=opposite_swings,reversal_since=reversal_since)
    if reversal:
        return dict(result,status='invalid',reason='confirmed_higher_reversal',invalidated=True)
    aligned=(d*(fast[-1]-slow[-1])>0 and d*(fast[-1]-fast[-2])>=0
             and d*(c15[-1]-c15[-2])>=0
             and (anchor is None or d*(price-anchor)>0))
    return dict(result,status='intact' if aligned else 'weakened',
                reason='larger_direction_intact' if aligned else 'mixed_evidence_not_reversal')


def entry_thesis_line(plan):
    thesis=plan.get('followup_thesis') or {}
    if thesis.get('policy')!=POLICY:
        return ''
    names={'breakout':'돌파 유지','trend_retest':'되돌림 재지지','range_rebound':'박스 경계 반등'}
    d=plan['direction'];anchor=thesis.get('anchor')
    level=(f"구조 기준 {anchor:,.1f} · 완료 5분봉 {'하회' if d==1 else '상회'} 시 무효화"
           if anchor is not None else '원래 손절·확인된 상위 반전으로 무효화')
    return f"📌 근거: {names.get(plan.get('strategy'),'가격 구조')} | {level}\n"
