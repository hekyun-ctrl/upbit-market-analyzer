"""Causal RSI/BB two-turn sequence; completed candles only, no future pivots.

The supplied video describes the sequence, not a maximum wait. Forty-eight
5m bars is an explicit implementation expiry, not an optimized parameter.
"""

MAX_SETUP_BARS = 48
POLICY = "rsi_two_turn_v2"


def engulfing(bars, i, direction):
    if i < 1:
        return False
    prev, cur = bars[i-1:i+1]
    d = direction
    return (d*(prev["close"]-prev["open"]) < 0 < d*(cur["close"]-cur["open"])
            and d*(cur["open"]-prev["close"]) <= 0
            and d*(cur["close"]-prev["open"]) >= 0)


def sequence_series(bars, indicators, direction):
    """One stage/event per input candle, invariant to any later candles.

The first extreme may extend until its first engulfing confirmation. A second
pullback must start AFTER that confirmation; one engulfing cannot satisfy both
turns. After an event (including a later cost rejection), a fresh exhaustion
setup is required, so the same bottom is not repeatedly offered as an entry.
"""
    d = direction
    key, band = ("low", "lower") if d == 1 else ("high", "upper")
    pending, result = None, []
    for i, (bar, bb) in enumerate(zip(bars, indicators, strict=True)):
        row = {"stage": "waiting_extreme", "event": None}
        result.append(row)
        if bb is None:
            row["stage"] = "indicator_warmup"
            continue
        if pending and i-pending["origin"] > MAX_SETUP_BARS:
            pending = None
            row["stage"] = "setup_expired"
        exhaustion = (
            bb["rsi"] is not None and d*(bb["rsi"]-50) < -20
            and d*(bar["close"]-bb[band]) < 0
            and (engulfing(bars, i, -d) or
                 i >= 2 and all(d*(b["close"]-b["open"]) < 0
                                for b in bars[i-2:i+1])))
        if exhaustion and (pending is None or pending["phase"] != "waiting_first_engulfing"):
            pending = dict(origin=i, first=i, phase="waiting_first_engulfing")
        elif pending:
            if pending["phase"] == "waiting_first_engulfing":
                if d*bar[key] < d*bars[pending["first"]][key]:
                    pending["first"] = i
                if engulfing(bars, i, d):
                    pending.update(first_confirmation=i, phase="waiting_pullback")
            else:
                if pending["phase"] == "waiting_pullback" and d*(bar["close"]-bar["open"]) < 0:
                    pending.update(second=i, pullback=i, phase="waiting_second_engulfing")
                elif pending["phase"] == "waiting_second_engulfing":
                    if d*bar[key] < d*bars[pending["second"]][key]:
                        pending["second"] = i
                    j = pending["second"]
                    inside = indicators[j]["lower"] <= bars[j][key] <= indicators[j]["upper"]
                    if engulfing(bars, i, d) and inside:
                        a, origin, first_confirm = (pending[k] for k in
                                                   ("first", "origin", "first_confirmation"))
                        row["event"] = dict(
                            lane="rsi_reversal", policy=POLICY, direction=d,
                            anchor=bars[j][key], trigger=bar["close"],
                            exhaustion_start=bars[origin]["start"],
                            exhaustion_rsi=indicators[origin]["rsi"],
                            exhaustion_close=bars[origin]["close"],
                            exhaustion_band=indicators[origin][band],
                            first_start=bars[a]["start"], first_price=bars[a][key],
                            first_rsi=indicators[a]["rsi"],
                            first_confirmation_start=bars[first_confirm]["start"],
                            pullback_start=bars[pending["pullback"]]["start"],
                            second_start=bars[j]["start"], second_price=bars[j][key],
                            second_rsi=indicators[j]["rsi"], second_inside_band=True,
                            second_confirmation_start=bar["start"],
                            bars_since_exhaustion=i-origin,
                            bars_since_first_confirmation=i-first_confirm,
                            setup_id=f"{POLICY}:{d}:{bars[origin]['start']}")
                        pending = None
                        row["stage"] = "confirmed"
        if pending:
            row.update(stage=pending["phase"],
                       exhaustion_start=bars[pending["origin"]]["start"],
                       first_start=bars[pending["first"]]["start"],
                       first_price=bars[pending["first"]][key])
            if "first_confirmation" in pending:
                row["first_confirmation_start"] = bars[pending["first_confirmation"]]["start"]
            if pending["phase"] == "waiting_second_engulfing":
                j = pending["second"]
                row.update(pullback_start=bars[pending["pullback"]]["start"],
                           second_start=bars[j]["start"], second_price=bars[j][key])
                if not indicators[j]["lower"] <= bars[j][key] <= indicators[j]["upper"]:
                    row["stage"] = "second_extreme_outside_band"
    return result
