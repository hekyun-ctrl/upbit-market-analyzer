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


def rank_entry_routes(state):
    """Return auditable, read-only priority from completed native bars.

    No synthetic closes, quote-as-candle, RSI extreme veto, or risk relaxation.
    A regime change never grants entry permission; existing independent
    plan/stop/target/context/confirmation gates remain authoritative.
    """
    empty = {"version": "trend_route_shadow_v1", "ready": False,
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
            "risk_gate": "unchanged",
            "delivery_policy": "unchanged"}
