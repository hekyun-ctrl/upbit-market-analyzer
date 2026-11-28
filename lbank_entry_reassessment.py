"""Causal triggers for an earlier review of LBank entry candidates."""

import math
from dataclasses import dataclass


def crossed_completed_level(state, route, previous_quote):
    """Return a breakout setup on a quote crossing established candle levels."""
    if previous_quote is None or not route or not route.get("ready"):
        return None
    if route.get("regime") not in ("impulse", "trend_pause"):
        return None
    direction = route.get("direction")
    if direction not in (1, -1):
        return None
    momentum = route.get("momentum_pct") or {}
    staircase = bool(route.get("shallow_pullback_continuation", {}).get("detected"))
    # A real impulse may break without a staircase pullback. Require sealed
    # 15-minute and hourly direction; no quote-only trend inference.
    impulse = (route.get("regime") == "impulse"
               and direction * momentum.get("15m", 0) > .12
               and direction * momentum.get("1h", 0) > .20)
    if not (staircase or impulse):
        return None
    momentum = route.get("momentum_pct", {}).get("1m_3close")
    if momentum is None or direction * momentum <= 0:
        return None

    five = state["five_bars"][-1]
    field = "high" if direction == 1 else "low"
    levels = [route[f"reference_{field}_6x5m"], five[field]]
    # These minutes closed after the last five-minute bar. A wick that has
    # already printed becomes part of the reference, even within the new 5m.
    levels.extend(bar[field] for bar in state["minute_bars"][-4:]
                  if bar["start"] >= five["start"] + 300)
    level = (max if direction == 1 else min)(levels)
    quote = state["quote"]
    if not (direction * (previous_quote - level) <= 0 < direction * (quote - level)):
        return None
    return {"strategy": "breakout", "direction": direction, "level": level}


@dataclass(frozen=True)
class QuoteDeviation:
    stage: str
    observed_at: float
    minute_start: int
    rejected_quote: float

    def allows_setup(self, minute_start):
        # A displaced watch must not be recreated from the same closed minute.
        return self.stage != "confirmation" or minute_start > self.minute_start

    def ready(self, state, minute_start, max_gap):
        if not self.allows_setup(minute_start):
            return False
        price, close = state["quote"], state["prices"][-1]
        return (math.isfinite(price) and math.isfinite(close) and min(price, close) > 0
                and abs(price / close - 1) <= max_gap)

    def evidence(self, state, minute_start, now, reason):
        return dict(stage=self.stage, rejected_at=self.observed_at,
                    rejected_quote=self.rejected_quote, rejected_minute=self.minute_start,
                    reassessed_at=now, reassessed_quote=state["quote"],
                    reassessed_minute=minute_start, evaluation_reason=reason)
