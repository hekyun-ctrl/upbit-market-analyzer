"""Conservative multi-timeframe context from LBank PERPETUAL TICKER samples.

Not OHLCV, not exchange candlesticks, not actual transaction volume. All
windows use *closed* timestamp buckets; a missing bucket or a discontinuous
recent feed fails closed. No trade entry decisions are made in this module.
"""
import math


def completed_sample_closes(observations, interval_seconds, n):
    """Return the last n completed bucket-end price samples, or None.

    Never consume the current open bucket; require a near-end observation in
    each closed bucket. A minute ticker does not reveal intra-minute trades.
    """
    if not observations or len(observations) < n * interval_seconds // 60:
        return None
    final_bucket = int(observations[-1][0] // interval_seconds) - 1
    last = {}
    for ts, px in observations:
        if not (math.isfinite(ts) and math.isfinite(px) and px > 0):
            return None
        k = int(ts // interval_seconds)
        if k <= final_bucket and ts - k * interval_seconds >= interval_seconds - 90:
            last[k] = px
    keys = range(final_bucket - n + 1, final_bucket + 1)
    if any(k not in last for k in keys):
        return None
    return [last[k] for k in keys]


def three_step_sample_reversal(five_minute_closes, direction):
    """A label ONLY: 3 material close-to-close moves then a reverse close.

    This is NOT Nassi TV's candle '3-tick rule': samples cannot show full bars,
    candle open/close, genuine highs/lows, or execution volume.
    """
    if len(five_minute_closes) < 5:
        return False
    a, b, c, d, e = five_minute_closes[-5:]
    if direction == 1:
        steps = (a - b, b - c, c - d)
        recovery = e - d
    else:
        steps = (b - a, c - b, d - c)
        recovery = d - e
    return (all(step / d >= .00025 for step in steps) and
            sum(steps) / d >= .0015 and recovery / d >= .00035)


def sampled_context_gate(observations, direction, strategy):
    """Return (allowed, reason, diagnostics) for manual-entry proposals.

    Derived from general 'avoid sideways, don't chase, check higher timeframe'
    principles, not copied promises of any investor's historical returns.
    Avoid contrarian fades of powerful 15-minute sampled movements.
    """
    if direction not in (1, -1):
        return False, "invalid_direction", None
    obs = list(observations or [])
    if len(obs) < 100:
        return False, "context_insufficient_samples", None
    recent = obs[-100:]
    if any(not (20 <= recent[i][0] - recent[i - 1][0] <= 150)
           for i in range(1, len(recent))):
        return False, "context_discontinuous_quotes", None
    b5 = completed_sample_closes(obs, 300, 8)
    b15 = completed_sample_closes(obs, 900, 5)
    if b5 is None or b15 is None:
        return False, "context_missing_closed_buckets", None
    range5 = (max(b5[-6:]) - min(b5[-6:])) / b5[-1]
    trend5 = b5[-1] / b5[-4] - 1
    trend15 = b15[-1] / b15[-4] - 1
    reversal = three_step_sample_reversal(b5, direction)
    ctx = {"range5_pct": range5 * 100, "trend5_pct": trend5 * 100,
           "trend15_pct": trend15 * 100, "three_step_recovery": reversal,
           "context_type": "sampled_perpetual_ticker_not_exchange_ohlcv"}
    if range5 < .0012:
        return False, "flat_sampled_market", ctx
    if direction * trend5 > .007:
        return False, "avoid_late_5m_impulse_chase", ctx
    if direction * trend15 < -.0050:
        return False, "avoid_15m_strong_countertrend", ctx
    # Trend/breakout entries require that both sampled timeframes are not
    # materially moving AGAINST the proposal. Range reactions already rely on
    # a verified structural reaction, so the stronger veto above suffices.
    if strategy in ("breakout", "trend_retest"):
        if direction * trend15 < -.0035 and direction * trend5 < -.0005:
            return False, "5m_15m_opposed_to_entry", ctx
    return True, "context_sampled_ok", ctx
