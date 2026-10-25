"""Closed first-retest entries and bounded location, never a profit forecast.

Only native completed minutes are used. Higher-timeframe direction cannot
substitute for a broken trigger, actual progress or room before a known pivot.
"""
import math

from lbank_entry_refinement import minute_atr
from lbank_native_entry import SOURCE
from lbank_trend_runner import ema_series

POLICY = 'native_first_retest_location_v2'


def valid_minutes(state):
    try:
        bars, prices, quote = state['minute_bars'], state['prices'], state['quote']
        atr = minute_atr(state)
        if (state['source'] != SOURCE or atr is None or len(bars) != len(prices)
                or any(type(b['start']) is not int for b in bars)
                or any(b['close'] != p for b, p in zip(bars, prices))
                or not math.isfinite(quote) or quote <= 0):
            return None
        return bars, atr
    except (KeyError, TypeError, ValueError):
        return None


def first_retests(state, diagnostics=None):
    """A real wick retest after a 10-minute break, first resumption within 3 bars.

    A shorter closed pattern replaces accumulated 5/15m momentum; it does not
    replace the caller's native context, RSI, cost or risk checks.
    """
    valid = valid_minutes(state)
    if valid is None:
        if diagnostics is not None:
            diagnostics.update(policy=POLICY, reason='invalid_completed_minutes')
        return []
    bars, atr = valid
    proposals, sides = [], {}
    for d in (1, -1):
        rows = [dict(o=d*b['open'], c=d*b['close'],
                     h=d*b['high' if d == 1 else 'low'],
                     l=d*b['low' if d == 1 else 'high']) for b in bars]
        closes = [r['c'] for r in rows]
        fast, slow = ema_series(closes, 9), ema_series(closes, 21)
        reason = 'no_recent_break_retest'
        if fast[-1] <= slow[-1] or fast[-1] <= fast[-2]:
            sides[str(d)] = 'minute_trend_not_aligned'
            continue
        for i in range(len(rows)-4, len(rows)-1):
            level = max(r['h'] for r in rows[i-10:i])
            impulse, last = rows[i], rows[-1]
            if impulse['c'] <= level or impulse['c']-impulse['o'] < .5*atr:
                continue
            tests, prior = rows[i+1:], rows[i+1:-1]
            low = min(r['l'] for r in tests)
            if not level-.5*atr <= low <= level+.25*atr:
                reason = 'level_not_retested_or_broken'
                continue
            # Earlier closed resumption must not become another fresh pattern.
            if any(r['c'] > level+.25*atr for r in prior):
                reason = 'resumption_already_passed'
                continue
            resume = max([level]+[r['h'] for r in prior]) if prior else max(level, impulse['c'])
            closed_resume = (last['c'] > resume and last['c'] > last['o']
                    and last['c'] > rows[-2]['c']
                    and (any(r['c'] < r['o'] for r in prior)
                         or last['l'] < min(last['o'], impulse['c'])))
            quote_resume = max([level]+[r['h'] for r in tests])
            quote = d*state['quote']
            quote_trigger = (all(r['c'] <= level+.25*atr for r in tests)
                             and any(r['c'] < r['o'] for r in tests)
                             and quote > quote_resume and quote > last['c'])
            if not closed_resume and not quote_trigger:
                reason = 'resumption_not_closed'
                continue
            if (quote if quote_trigger else last['c'])-level > 1.5*atr:
                reason = 'resumption_already_extended'
                continue
            if quote_trigger:
                resume = quote_resume
            proof = dict(policy=POLICY, pattern_start=bars[i]['start'],
                         signal_start=bars[-1]['start'], trigger=d*level,
                         resume_level=d*resume, atr1=atr,
                         expires_bar_end=bars[i]['start']+60+4*60,
                         source='completed_retest_live_quote' if quote_trigger else 'completed_native_1m',
                         first_retest=True)
            proposals.append(dict(strategy='trend_retest', direction=d,
                                  level=d*level, structure_anchor=d*low,
                                  entry_location=proof))
            reason = 'first_retest_closed'
            break
        sides[str(d)] = reason
    if diagnostics is not None:
        diagnostics.update(policy=POLICY, atr1=atr, sides=sides,
                           proposals=len(proposals))
    return proposals


def location_check(state, direction, setup):
    valid = valid_minutes(state)
    if valid is None or direction not in (-1, 1):
        return False, 'location_data_unavailable', None
    bars, atr = valid
    price, strategy = state['quote'], setup.get('strategy', 'breakout')
    proof = dict(policy=POLICY, atr1=atr, strategy=strategy)
    try:
        if strategy == 'range_rebound':
            return True, 'range_structure_unchanged', proof
        anchor = setup.get('level') if strategy == 'breakout' else setup.get('structure_anchor')
        if anchor is None or not math.isfinite(anchor) or anchor <= 0:
            return False, 'location_anchor_unavailable', proof
        distance = direction*(price-anchor)/atr
        limit = 1.5 if strategy == 'breakout' else 2.
        proof.update(anchor=anchor, extension_atr1=distance, max_extension_atr1=limit)
        if not 0 < distance <= limit:
            return False, 'entry_location_extended_or_lost', proof
        frozen = setup.get('entry_location')
        if frozen:
            trigger = frozen['trigger']
            available = {b['start']:b for b in bars}
            impulse = available.get(frozen['pattern_start'])
            if (frozen['policy'] != POLICY or impulse is None
                    or frozen['source'] not in ('completed_native_1m', 'completed_retest_live_quote')
                    or direction*(impulse['close']-trigger) <= 0
                    or bars[-1]['start']+60 > frozen['expires_bar_end']
                    or direction*(price-trigger) <= 0
                    or direction*(price-trigger) > 1.5*atr):
                return False, 'first_retest_evidence_lost_or_expired', proof
        return True, 'entry_location_current', proof
    except (KeyError, TypeError, ValueError):
        return False, 'location_evidence_invalid', proof


def confirmation_check(state, watch):
    """Fresh progress and every post-watch closed bar, without another fixed wait."""
    ok, reason, proof = location_check(state, watch['direction'], watch)
    if not ok:
        return ok, reason, proof
    try:
        d, quote, price = watch['direction'], state['quote'], watch['price']
        # Freeze the risk scale known at discovery, not a later volatility fall.
        atr = (watch.get('location_evidence') or {}).get('atr1', proof['atr1'])
        if not math.isfinite(atr) or atr <= 0:
            return False, 'location_evidence_invalid', proof
        required = max(.1, .1*atr)
        progress = d*(quote-price)
        proof.update(progress=progress, required_progress=required,
                     minimum_confirmation_seconds=30, extra_wait_seconds=0)
        if progress < required:
            return False, 'quote_not_progressing_from_watch', proof
        later = [b for b in state['minute_bars'] if b['start'] > watch['native_watch_1m_start']]
        key = 'low' if d == 1 else 'high'
        if any(d*(b[key]-watch['stop']) <= 0 for b in later):
            return False, 'watch_structure_stop_crossed', proof
        frozen = watch.get('entry_location')
        if frozen:
            resume = frozen['resume_level']
            if d*(quote-resume) <= 0 or any(d*(b['close']-frozen['trigger']) <= 0 for b in later):
                return False, 'first_retest_level_lost', proof
        return True, 'fresh_location_confirmed', proof
    except (KeyError, TypeError, ValueError):
        return False, 'location_evidence_invalid', proof
