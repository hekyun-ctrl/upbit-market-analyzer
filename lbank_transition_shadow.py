"""Research-only structural reversal evidence. No trade or alert permission."""
import math

VERSION = 'transition_structure_shadow_v1'

def transition_evidence(bars, asof):
    result = dict(version=VERSION, ready=False, direction=0, stage='unverified',
                  approval=False, entry_prices=None, risk_gate='unchanged')
    if not isinstance(bars, list) or len(bars) < 4:
        return dict(result, reason='insufficient_bars')
    tail = bars[-4:]
    try:
        if not math.isfinite(float(asof)):
            raise ValueError('asof')
        starts = [int(b['start']) for b in tail]
        values = [[float(b[k]) for k in ('open','high','low','close')] for b in tail]
        if any(b.get('quality') != 'stream_closed' for b in tail):
            raise ValueError('quality')
        if any(s % 300 or s + 300 > asof for s in starts) or any(b-a != 300 for a,b in zip(starts,starts[1:])):
            raise ValueError('time')
        if any(not all(math.isfinite(x) and x > 0 for x in row) or row[1] < max(row) or row[2] > min(row) for row in values):
            raise ValueError('ohlc')
    except (KeyError, TypeError, ValueError, OverflowError):
        return dict(result, reason='invalid_or_unavailable_completed_bars')
    highs = [r[1] for r in values]; lows = [r[2] for r in values]; closes = [r[3] for r in values]
    higher_low = min(lows[2:]) > min(lows[:2])
    lower_high = max(highs[2:]) < max(highs[:2])
    up = higher_low and closes[-1] > closes[-2] > closes[-3]
    down = lower_high and closes[-1] < closes[-2] < closes[-3]
    direction = 1 if up else -1 if down else 0
    barrier = max(highs[1:3]) if direction == 1 else min(lows[1:3]) if direction == -1 else None
    broken = (direction == 1 and closes[-1] > barrier) or (direction == -1 and closes[-1] < barrier)
    return dict(result, ready=True, direction=direction,
                stage='structure_break' if broken else 'early_recovery' if direction else 'unresolved',
                barrier=barrier, close=closes[-1], bar_start=starts[-1],
                reason='closed_bar_structure_only')
