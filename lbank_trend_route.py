"""Read-only trend-adaptive route ranking for LBank research.

It never creates an entry plan, changes risk gates, sends an alert, or trades.
Only complete contiguous native candles supplied by NativeEntryFeed are used.
1h/4h are derived from complete 5m candles, NOT exchange-verified candles.
"""
import math

ROUTES = ("breakout", "continuation", "pullback")


def _finite(value):
    try:
        value = float(value)
        return value if math.isfinite(value) and value > 0 else None
    except (TypeError, ValueError, OverflowError):
        return None


def _direction(move, threshold):
    return 1 if move > threshold else -1 if move < -threshold else 0


def _staircase_evidence(five, direction):
    """Completed-bar evidence for an impulse with only shallow counter-moves.

    An observed pattern, NOT an entry signal or an invitation to chase.
    All price comparisons use fully closed 5m candles, excluding live quotes.
    """
    if direction not in (-1, 1) or len(five) < 13:
        return {"detected": False, "reason": "insufficient_or_neutral"}
    closes = [float(b["close"]) for b in five[-13:]]
    highs = [float(b["high"]) for b in five[-13:]]
    lows = [float(b["low"]) for b in five[-13:]]
    recent = closes[-9:]
    signed_steps = [direction * (b / a - 1) * 100
                    for a, b in zip(recent, recent[1:])]
    directional_progress = direction * (recent[-1] / recent[0] - 1) * 100
    adverse_steps = [abs(x) for x in signed_steps if x < 0]
    max_adverse = max(adverse_steps, default=0.)
    follow_steps = sum(step > 0 for step in signed_steps)
    prior_extreme = max(highs[-7:-1]) if direction == 1 else min(lows[-7:-1])
    last_close = closes[-1]
    # An unfinished intrabar wick must not qualify as a breakout.
    breakout_completed = direction * (last_close / prior_extreme - 1) > 0
    # A completed candle just below a new extreme can still be a continuation
    # candidate; the independent breakout route is not silently redefined.
    distance_to_extreme_pct = (max(0., (prior_extreme / last_close - 1) * 100)
                               if direction == 1 else
                               max(0., (last_close / prior_extreme - 1) * 100))
    detected = (directional_progress >= .65
                and follow_steps >= 5
                and max_adverse <= .35
                and distance_to_extreme_pct <= .30)
    return {
        "detected": detected,
        "reason": "shallow_pullback_staircase" if detected else "pattern_not_confirmed",
        "directional_progress_pct": round(directional_progress, 4),
        "positive_steps_8": follow_steps,
        "max_adverse_closed_step_pct": round(max_adverse, 4),
        "distance_to_prior_extreme_pct": round(distance_to_extreme_pct, 4),
        "completed_bar_breakout": breakout_completed,
        "uses_quote_for_trigger": False,
    }


def rank_entry_routes(state):
    """Return auditable, read-only priority from completed native bars.

    No synthetic closes, quote-as-candle, RSI extreme veto, or risk relaxation.
    A regime change never grants entry permission; existing independent
    plan/stop/target/context/confirmation gates remain authoritative.
    """
    empty = {"version": "trend_route_shadow_v2", "ready": False,
             "regime": "unverified", "direction": 0, "routes": [],
             "approval": False, "entry_prices": None,
             "higher_timeframes": "not_independently_verified"}
    if not isinstance(state, dict):
        return dict(empty, reason="no_native_state")
    five = state.get("five_bars")
    one = state.get("minute_bars")
    quote = _finite(state.get("quote"))
    if not isinstance(five, list) or not isinstance(one, list) or quote is None:
        return dict(empty, reason="missing_bars_or_quote")
    if len(five) < 25 or len(one) < 20:
        return dict(empty, reason="insufficient_completed_bars")
    # Reject gaps, forming, mislabeled, or malformed candles. Native feed
    # already certifies these, but fail closed if invoked on other inputs.
    for bars, seconds in ((five, 300), (one, 60)):
        tail = bars[-max(25 if seconds == 300 else 20, 2):]
        for i, bar in enumerate(tail):
            if not isinstance(bar, dict) or bar.get("quality") != "stream_closed":
                return dict(empty, reason="unverified_candle")
            try:
                start = int(bar["start"])
                o, h, l, c = (float(bar[k]) for k in ("open", "high", "low", "close"))
            except (KeyError, TypeError, ValueError, OverflowError):
                return dict(empty, reason="invalid_candle")
            if start % seconds or min(o, h, l, c) <= 0 or not all(
                    math.isfinite(x) for x in (o, h, l, c)) or h < max(o, c, l) or l > min(o, c, h):
                return dict(empty, reason="invalid_candle")
            if i and start - int(tail[i - 1]["start"]) != seconds:
                return dict(empty, reason="gap_in_completed_bars")
    close5 = [float(b["close"]) for b in five]
    close1 = [float(b["close"]) for b in one]
    move5 = (close5[-1] / close5[-2] - 1) * 100
    move15 = (close5[-1] / close5[-4] - 1) * 100
    move60 = (close5[-1] / close5[-13] - 1) * 100
    move1 = (close1[-1] / close1[-4] - 1) * 100
    # Longer horizons are descriptive context only. No 4h inference unless
    # enough finished contiguous native 5m candles exist.
    move4h = (close5[-1] / close5[-49] - 1) * 100 if len(close5) >= 49 else None
    direction = _direction(move15, 0.12)
    if direction and _direction(move60, 0.20) == -direction:
        regime = "conflicted"
    elif direction and _direction(move5, 0.05) == direction:
        regime = "impulse"
    elif direction:
        regime = "trend_pause"
    else:
        regime = "range_or_transition"
    # Rank the fast routes FIRST in a strong impulse, without requiring
    # a deep pullback. No signal is inferred from price touching a level.
    if regime == "impulse":
        routes = ["breakout", "continuation", "pullback"]
    elif regime == "trend_pause":
        routes = ["continuation", "breakout", "pullback"]
    elif regime == "conflicted":
        routes = ["pullback", "continuation", "breakout"]
    else:
        routes = ["pullback", "continuation", "breakout"]
    staircase = _staircase_evidence(five, direction)
    prior_high = max(float(b["high"]) for b in five[-7:-1])
    prior_low = min(float(b["low"]) for b in five[-7:-1])
    return {"version": "trend_route_shadow_v1", "ready": True,
            "direction": direction, "regime": regime, "routes": routes,
            "approval": False, "entry_prices": None,
            "higher_timeframes": "derived_5m_not_exchange_verified",
            "momentum_pct": {"1m_3close": round(move1, 4),
                             "5m": round(move5, 4),
                             "15m": round(move15, 4),
                             "1h": round(move60, 4),
                             "4h": round(move4h, 4) if move4h is not None else None},
            "reference_high_6x5m": round(prior_high, 2),
            "reference_low_6x5m": round(prior_low, 2),
            "shallow_pullback_continuation": staircase,
            "risk_gate": "unchanged",
            "delivery_policy": "unchanged"}
