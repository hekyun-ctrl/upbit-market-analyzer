"""Closed-1m continuation candidate after a shallow trend pullback.

This module recognizes a *candidate*, not an entry approval. No quote is
fabricated as a candle; no order or Telegram call is made here. Existing
context, cost, stop, target, quality and stage-two confirmation gates remain
authoritative in lbank_futures_monitor.
"""
import math
from statistics import median

from lbank_entry_refinement import minute_atr


def detect_fast_resumption(state, route):
    result = {"status": "no_candidate", "policy": "closed_1m_trend_resumption_v1",
              "approval": False}
    if not state or not route or not route.get("ready"):
        return dict(result, status="native_or_trend_unavailable")
    direction = route.get("direction")
    if direction not in (-1, 1) or route.get("regime") not in ("impulse", "trend_pause"):
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
    if not (d*(close-float(prev["close"])) >= max(.00010*close, .18*atr)
            and d*(float(prev["close"])-float(before["close"])) > 0
            and d*(close-float(last["open"])) >= max(.00010*close, .18*atr)
            and d*(float(prev["close"])-float(prev["open"])) > 0):
        return dict(result, status="one_minute_reacceleration_missing")
    # Use a prior observed adverse excursion, never the current quote as support.
    probe = minutes[-9:-2]
    earlier = minutes[-16:-9]
    low = min(float(b["low"]) for b in probe)
    high = max(float(b["high"]) for b in probe)
    prior = (max(float(b["high"]) for b in earlier) if d == 1
             else min(float(b["low"]) for b in earlier))
    anchor = low if d == 1 else high
    pullback = d*(prior-anchor) / close
    recovery = d*(close-anchor)
    if not (.00065 <= pullback <= .008
            and recovery >= max(.00055*close, .65*atr)
            and recovery <= max(.008*close, 4.0*atr)):
        return dict(result, status="pullback_or_recovery_outside_structure")
    # Relative traded volume is a research criterion, not a promise of execution.
    baseline = median(float(b["volume"]) for b in minutes[-12:-2])
    if baseline <= 0 or float(last["volume"]) < baseline*1.1:
        return dict(result, status="recovery_volume_insufficient")
    # A stop is anchored to this observed swing by the existing cost/risk planner.
    # Quote chase checks and the next confirmation occur in the existing pipeline.
    return dict(result, status="candidate", direction=d,
                setup={"strategy": "trend_retest", "direction": d,
                       "level": close, "structure_anchor": anchor,
                       "source": "closed_1m_fast_resumption"},
                last_closed_1m_start=last["start"],
                pullback_pct=round(100*pullback, 4),
                recovery_atr=round(recovery/atr, 3),
                volume_multiple=round(float(last["volume"])/baseline, 3))
