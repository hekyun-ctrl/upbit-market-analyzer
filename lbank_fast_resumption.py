"""Closed-1m continuation candidate after a shallow trend pullback.

This module recognizes a *candidate*, not an entry approval. No quote is
fabricated as a candle; no order or Telegram call is made here. Existing
context, cost, stop, target, quality and stage-two confirmation gates remain
authoritative in lbank_futures_monitor.
"""
import math
from statistics import median

from lbank_entry_refinement import minute_atr



def confirmed_recent_retest_anchor(minutes, direction, old_anchor, atr):
    """Later verified higher-low/lower-high, not a tighter arbitrary wick.

    Only a completed two-sided 1m pivot, formed AFTER the older swing, can
    replace that swing for a continuation. Every candle after the pivot must
    respect it. On uncertainty preserve the original, more distant anchor.
    This never approves an entry or alters monetary/cost/target gates.
    """
    window = minutes[-9:]
    if len(window) != 9 or direction not in (-1, 1) or atr <= 0:
        return None
    d = direction
    key = "low" if d == 1 else "high"
    older_i = min(range(7), key=lambda i: d * float(window[i][key]))
    # Two completed bars on EACH side, excluding the two recovery candles
    # as possible pivot locations. The recovered level must remain intact.
    for i in range(5, 1, -1):
        if i <= older_i:
            continue
        pivot = float(window[i][key])
        if d * (pivot - old_anchor) < max(.30 * atr, .00015 * float(window[-1]["close"])):
            continue
        neighbors = window[i-2:i] + window[i+1:i+3]
        if any(d * (float(bar[key]) - pivot) < -.05 * atr for bar in neighbors):
            continue
        if min(d * (float(window[i-2][key]) - pivot),
               d * (float(window[i+2][key]) - pivot)) < .20 * atr:
            continue
        if any(d * (float(bar[key]) - pivot) < -.05 * atr for bar in window[i+3:]):
            continue
        return pivot
    return None


def detect_fast_resumption(state, route):
    result = {"status": "no_candidate", "policy": "closed_1m_trend_resumption_v2",
              "approval": False}
    if not state or not route or not route.get("ready"):
        return dict(result, status="native_or_trend_unavailable")
    direction = route.get("direction")
    regime = route.get("regime")
    conflict_recovery = False
    if regime == "conflicted":
        # 15m and 1h disagree. A completed, strong 1m recovery aligned
        # with the verified 1h move can be SCREENED, not automatically approved.
        # The normal 5m/15m context, RSI, costs and risk rules still decide.
        momentum = route.get("momentum_pct") or {}
        try:
            hourly = float(momentum["1h"])
            if not math.isfinite(hourly) or abs(hourly) < .35:
                return dict(result, status="conflict_hourly_direction_unproven")
            hourly_direction = 1 if hourly > 0 else -1
            if direction != -hourly_direction:
                return dict(result, status="conflict_hourly_direction_unproven")
            direction = hourly_direction
            conflict_recovery = True
        except (KeyError, ValueError, TypeError):
            return dict(result, status="conflict_hourly_direction_unproven")
    elif direction not in (-1, 1) or regime not in ("impulse", "trend_pause"):
        return dict(result, status="trend_conflicted_or_neutral")
    minutes = state.get("minute_bars")
    closes = state.get("prices")
    if not isinstance(minutes, list) or len(minutes) < 65 or not isinstance(closes, list):
        return dict(result, status="minute_history_insufficient")
    if len(closes) != len(minutes):
        return dict(result, status="minute_close_misaligned")
    # A caller cannot pass a shifted, fabricated or unclosed 1m series.
    for i, b in enumerate(minutes[-16:]):
        try:
            o, h, l, c, v = [float(b[k]) for k in ("open", "high", "low", "close", "volume")]
            start = b["start"]
            if (b.get("quality") != "stream_closed" or type(start) is not int
                    or start % 60 or not all(math.isfinite(x) for x in (o, h, l, c, v))
                    or not 0 < l <= min(o, c) <= max(o, c) <= h or v < 0
                    or (i and start != minutes[-16+i-1]["start"]+60)
                    or abs(c-float(closes[-16+i])) > 1e-8):
                return dict(result, status="minute_source_invalid")
        except (KeyError, ValueError, TypeError, OverflowError):
            return dict(result, status="minute_source_invalid")
    atr = minute_atr(state)
    if atr is None:
        return dict(result, status="minute_atr_invalid")
    last, prev, before = minutes[-1], minutes[-2], minutes[-3]
    close = float(last["close"])
    d = direction
    minimum_reaction = max(.00010 * close, .18 * atr)
    if min(d * (close - float(prev["close"])),
           d * (close - float(last["open"]))) < minimum_reaction:
        return dict(result, status="one_minute_reacceleration_missing")
    two_minute = (d * (float(prev["close"]) - float(before["close"])) > 0
                  and d * (float(prev["close"]) - float(prev["open"])) > 0)
    adverse, favorable = ("low", "high") if d == 1 else ("high", "low")
    probe = minutes[-9:-2] if two_minute else minutes[-9:-1]
    anchor = (min if d == 1 else max)(float(b[adverse]) for b in probe)
    original_anchor = anchor
    anchor_policy = "older_completed_swing"
    if two_minute and not conflict_recovery:
        later = confirmed_recent_retest_anchor(minutes, d, anchor, atr)
        if later is not None:
            anchor = later
            anchor_policy = "two_sided_closed_1m_higher_low_or_lower_high"
    route_name = "two_minute_reacceleration"
    if not two_minute:
        # A single sealed reaction is useful after two distinct tests of the
        # same area. The bounce between tests distinguishes two retests from
        # adjacent bars sharing the same price at their boundary.
        touches = [i for i, bar in enumerate(probe)
                   if d * (float(bar[adverse]) - anchor) <= .25 * atr]
        held_twice = (len(touches) >= 2 and touches[-1] - touches[0] >= 2
                      and any(d * (float(bar["close"]) - anchor) >= .5 * atr
                              for bar in probe[touches[0] + 1:touches[-1]]))
        reclaim = (d * (float(last[adverse]) - anchor) >= 0
                   and d * (close - float(prev[favorable])) > 0)
        if not (held_twice and reclaim):
            return dict(result, status="one_minute_reacceleration_missing")
        route_name = "repeated_level_reclaim"

    earlier = minutes[-16:-9]
    prior = (max if d == 1 else min)(float(b[favorable]) for b in earlier)
    pullback = d*(prior-anchor) / close
    recovery = d*(close-anchor)
    if not (.00065 <= pullback <= .008
            and recovery >= max(.00055*close, .65*atr)
            and recovery <= max(.008*close, 4.0*atr)):
        return dict(result, status="pullback_or_recovery_outside_structure")
    # A conflicted 15m/1h recovery must break actual completed minute wicks;
    # a two-candle bounce without that structural reclaim remains observation.
    if conflict_recovery:
        if not two_minute:
            return dict(result, status="conflict_requires_two_closed_recovery_minutes")
        prior_wick = (max if d == 1 else min)(
            float(b[favorable]) for b in minutes[-4:-1])
        if d * (close - prior_wick) < max(.00005 * close, .12 * atr):
            return dict(result, status="conflict_wick_reclaim_missing")
    # Relative traded volume is a research criterion, not a promise of execution.
    baseline = median(float(b["volume"]) for b in minutes[-12:-2])
    min_multiple = 1.30 if conflict_recovery else 1.10
    if baseline <= 0 or float(last["volume"]) < baseline*min_multiple:
        return dict(result, status="recovery_volume_insufficient")
    # A stop is anchored to this observed swing by the existing cost/risk planner.
    # Quote chase checks and the next confirmation occur in the existing pipeline.
    # Only the existing two-candle pattern enters the standard approval path.
    # A single reclaim is evaluated and recorded for prospective validation.
    return dict(result, status="candidate" if two_minute else "early_reclaim", direction=d,
                higher_timeframe_conflict=conflict_recovery,
                confirmation_policy="existing_full_qualification_and_stage_two",
                reaction="hour_aligned_conflict_recovery" if conflict_recovery else route_name,
                setup={"strategy": "trend_retest", "direction": d,
                       "level": close, "structure_anchor": anchor,
                       "source": "closed_1m_fast_resumption"},
                last_closed_1m_start=last["start"],
                anchor_policy=anchor_policy,
                original_structure_anchor=original_anchor,
                pullback_pct=round(100*pullback, 4),
                recovery_atr=round(recovery/atr, 3),
                volume_multiple=round(float(last["volume"])/baseline, 3))
