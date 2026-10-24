"""Bounded entry evidence hierarchy, independent of frozen followup policies.

Two aligned completed 15m changes plus aligned 5m EMA direction take priority
over isolated 5m pivots and minor reaction tests. Repeated barriers, late price
extension and monetary risk remain constraints. Parameters are operational
hypotheses, not fitted win probabilities or a profitability certification.
"""
import math

from lbank_entry_quality import direction_priority, confirmed_pivot_barriers
from lbank_trend_runner import atr_wilder, ema_series

POLICY = 'native_ranked_entry_v1'
MAX_EXTENSION_ATR = 2.5
MAX_TREND_TARGET_ATR = 6.0
BARRIER_CLUSTER_ATR = .25


def ranked_entry_evidence(state, direction, price, target=None, now=None):
    unknown = dict(policy=POLICY, ready=False, higher_direction_intact=False,
                   location_ok=True, may_override_minor_reaction=False,
                   may_override_single_pivots=False, atr_target_exception=False,
                   repeated_barrier=False, barrier_groups=[], overrides=[])
    try:
        if now is None:
            now = state['minute_bars'][-1]['start']+60
        upper = direction_priority(state,direction,price,target,now)
        if not upper.get('ready'):
            return unknown
        bars = state['five_bars']
        atr = atr_wilder(bars)
        if atr is None or not math.isfinite(atr) or atr <= 0:
            return unknown
        ema9 = ema_series([b['close'] for b in bars],9)[-1]
        extension = direction*(price-ema9)/atr
        location_ok = extension <= MAX_EXTENSION_ATR
        # Direction is evaluated before target obstacles. A single obstacle
        # must not circularly erase the evidence used to rank that obstacle.
        higher = bool(upper['support_intact'] and upper['trend15_aligned']
                      and upper['ema_aligned'])
        obstacles = ([] if target is None else
            [b for b in confirmed_pivot_barriers(bars,direction,price)
             if direction*(b['price']-target) < price*.00015])
        groups = []
        for b in sorted(obstacles,key=lambda b:direction*b['price']):
            matching = next((g for g in groups
                             if abs(b['price']-g['price']) <= BARRIER_CLUSTER_ATR*atr),None)
            if matching is None:
                groups.append(dict(price=b['price'],starts=[b['start']],touches=1))
            elif b['start'] not in matching['starts']:
                matching['starts'].append(b['start'])
                matching['touches'] += 1
        repeated = any(g['touches'] >= 2 for g in groups)
        ranked = higher and location_ok and not repeated
        return dict(unknown,ready=True,bar_start=bars[-1]['start'],
                    higher_direction_intact=higher,upper_evidence=upper,
                    atr5=atr,ema9=ema9,extension_atr=extension,
                    max_extension_atr=MAX_EXTENSION_ATR,location_ok=location_ok,
                    target_distance_atr=(abs(target-price)/atr if target is not None else None),
                    max_trend_target_atr=MAX_TREND_TARGET_ATR,
                    may_override_minor_reaction=bool(ranked),
                    may_override_single_pivots=bool(ranked and groups),
                    atr_target_exception=bool(ranked and target is not None
                        and abs(target-price) <= MAX_TREND_TARGET_ATR*atr),
                    repeated_barrier=repeated,barrier_groups=groups)
    except (KeyError,TypeError,ValueError,IndexError,OverflowError):
        return unknown
