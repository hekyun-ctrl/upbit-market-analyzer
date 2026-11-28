"""A completed 5-minute ticker-sample RSI reversal can pause before resuming.

This is NOT LBank's authenticated futures candle RSI or an order-book reading.
Never returns an actionable recommendation by itself; caller must also pass
structure, multi-timeframe, fee-aware RR, chase and stop-risk gates.
"""


def consolidation_reversal_resume(closes, values, direction, max_pause_bars=12):
    """Find earlier <=30/>=70 extreme, first turn, 1-12 pauses, then breakout.

    A second consecutive rising/falling RSI bar is *not* required. But the
    latest fully CLOSED bucket must break the plateau with price AND RSI.
    No incomplete candles, no forward information, no near-miss exceptions.
    """
    if direction not in (1, -1):
        return False, "invalid_direction"
    if len(values) < 14 or len(closes) < len(values):
        return False, "consolidation_not_enough_history"
    n = len(values)
    # Align RSI samples to the close they describe.
    p = closes[-n:]
    v = values[-n:]
    current = v[-1]
    if not (30 < current < 60 if direction == 1 else 50 < current < 70):
        return False, "consolidation_rsi_not_recovered_or_extended"
    if direction * (v[-1] - v[-2]) < 1.2:
        return False, "consolidation_no_rsi_reacceleration"
    if direction * (p[-1] / p[-2] - 1) < .00035:
        return False, "consolidation_no_price_reacceleration"

    # Every candidate anchor is in the *past*. Require at least one completed
    # plateau bucket between the first reversal and the latest breakout.
    for anchor in range(n - 4, max(-1, n - max_pause_bars - 4), -1):
        if anchor < 0:
            continue
        if not (v[anchor] <= 30 if direction == 1 else v[anchor] >= 70):
            continue
        if direction * (v[anchor + 1] - v[anchor]) < 1.2:
            continue
        if direction * (p[anchor + 1] / p[anchor] - 1) < .00020:
            continue

        plateau = p[anchor + 1:-1]
        plateau_rsi = v[anchor + 1:-1]
        if not (2 <= len(plateau) <= max_pause_bars + 1):
            continue
        if (max(plateau) - min(plateau)) / p[-1] > .0024:
            continue
        # Reject a fresh extreme or a retest that undercuts the original pivot.
        if direction == 1:
            if min(p[anchor + 1:]) < p[anchor] * .9998:
                continue
            if min(plateau_rsi) <= 30:
                continue
            if p[-1] < max(plateau) * 1.00035:
                continue
            if v[-1] < max(plateau_rsi) + 1.2:
                continue
        else:
            if max(p[anchor + 1:]) > p[anchor] * 1.0002:
                continue
            if max(plateau_rsi) >= 70:
                continue
            if p[-1] > min(plateau) * .99965:
                continue
            if v[-1] > min(plateau_rsi) - 1.2:
                continue
        return True, "5m_extreme_then_first_turn_plateau_resume"
    return False, "consolidation_no_valid_extreme_pause_breakout"
