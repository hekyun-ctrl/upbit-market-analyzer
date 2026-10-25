"""Opt-in entry hypotheses; parameters fixed before the paired comparison.

No data substitution, RSI/higher-timeframe veto changes, fee discounts, sizing
increases, or proven-profit claims. Production defaults to the legacy policy.
"""
import math

from lbank_trend_runner import atr_wilder

MODES = ('legacy', 'structure_cost', 'capture', 'location')


def minute_atr(state):
    try:
        bars = state['minute_bars']
        if len(bars) < 65:
            return None
        for i, b in enumerate(bars):
            values = [float(b[k]) for k in ('open', 'high', 'low', 'close')]
            o, h, l, c = values
            if (not all(math.isfinite(v) and v > 0 for v in values)
                    or l > min(o, c) or h < max(o, c)
                    or b['quality'] != 'stream_closed' or b['start'] <= 0 or b['start'] % 60
                    or (i and b['start'] != bars[i-1]['start'] + 60)):
                return None
        value = atr_wilder(bars)
        return value if value is not None and math.isfinite(value) and value > 0 else None
    except (KeyError, TypeError, ValueError, OverflowError):
        return None


def local_stop_floor(state, legacy_floor):
    """Noise floor for a 1m setup; the caller still places stops beyond anchors."""
    atr = minute_atr(state)
    try:
        quote = float(state['quote'])
        if (atr is None or not math.isfinite(quote) or quote <= 0
                or not math.isfinite(legacy_floor) or legacy_floor <= 0):
            return None
        return dict(floor=min(legacy_floor, max(.0005, 1.5 * atr / quote)),
                    atr1=atr, basis='1m_structure_with_1_5atr_noise_floor')
    except (KeyError, TypeError, ValueError, OverflowError):
        return None


def pullback_floor(atr5, quote):
    """One completed 5m ATR, bounded by the existing .055% reaction and .15%."""
    return min(.0015, max(.00055, atr5 / quote))


def impulse_breakout(prices, state, ema, prior_high, prior_low):
    """Closed body, actual wick break, positive 5/15m progress and 1m EMA trend."""
    result = dict(long=False, short=False, atr1=None, minimum_body=None)
    atr = minute_atr(state)
    if atr is None:
        return result
    if len(prices) != len(state['minute_bars']) or any(
            p != b['close'] for p, b in zip(prices, state['minute_bars'])):
        return result
    bar = state['minute_bars'][-1]
    current = prices[-1]
    body = current - bar['open']
    minimum = max(current * .00055, atr)
    fast, slow = ema(prices, 20), ema(prices, 50)
    long = (current > prior_high and body >= minimum and current > prices[-6]
            and current > prices[-16] and fast[-1] > slow[-1] and fast[-1] > fast[-2])
    short = (current < prior_low and -body >= minimum and current < prices[-6]
             and current < prices[-16] and fast[-1] < slow[-1] and fast[-1] < fast[-2])
    return dict(long=long, short=short, atr1=atr, minimum_body=minimum)
