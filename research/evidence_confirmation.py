"""Research-only E3 readiness. No clock spoofing, sending, or model tuning."""
import math

from lbank_native_entry import SOURCE

POLICY = 'p2_e3_frozen_trigger_no_fixed_wait_v1'


def freeze_trigger(setup, state):
    """Only a line already observable at discovery; never a future extreme."""
    d = setup['direction']
    bar = state['minute_bars'][-1]
    price = setup.get('level') if setup.get('strategy', 'breakout') == 'breakout' else bar['high' if d == 1 else 'low']
    return dict(policy=POLICY, price=price, bar_start=bar['start'], known_at=None,
                basis='original_breakout_level' if setup.get('strategy', 'breakout') == 'breakout'
                else 'last_completed_minute_directional_extreme')


def readiness(state, watch, now, nominal_delay=0):
    """Conservative first post-watch bar check; ambiguous pre/post touch rejects.

    Bars have unknown real receipts in this development replay. Nominal
    availability is only the explicitly declared scenario, not a receipt claim.
    Terminal stop failures are sticky: the runner discards the watch.
    """
    proof = dict(policy=POLICY, known_at=None, actual_received_at=None,
                 timing_basis='retained_quote_proxy_and_nominal_bar_scenario')
    try:
        d, stop, quote = watch['direction'], watch['stop'], state['quote']
        bars = state['minute_bars']
        start = watch['native_watch_1m_start']
        trigger = watch['_p2_confirmation_trigger']
        if (state['source'] != SOURCE or d not in (-1, 1) or not bars
                or not all(math.isfinite(v) and v > 0 for v in (stop, quote, trigger['price']))
                or not math.isfinite(now) or nominal_delay < 0
                or trigger['policy'] != POLICY or trigger['bar_start'] != start
                or not any(b['start'] == start for b in bars)):
            return False, 'e3_invalid_evidence', proof, True
        for bar in bars:
            values = [bar[k] for k in ('open', 'high', 'low', 'close')]
            if (bar['quality'] != 'stream_closed' or type(bar['start']) is not int
                    or bar['start'] % 60 or bar['start']+60+nominal_delay > now
                    or (bar.get('received_at') is not None and bar['received_at'] > now)
                    or not all(math.isfinite(v) and v > 0 for v in values)
                    or bar['low'] > min(bar['open'], bar['close'])
                    or bar['high'] < max(bar['open'], bar['close'])):
                return False, 'e3_invalid_or_unavailable_bar', proof, True
        if any(b['start']-a['start'] != 60 for a, b in zip(bars, bars[1:])):
            return False, 'e3_native_gap', proof, True
        after = [b for b in bars if b['start'] > start]
        key = 'low' if d == 1 else 'high'
        proof.update(trigger=trigger, quote=quote, stop=stop,
                     checked_post_watch_starts=[b['start'] for b in after])
        if d*(quote-stop) <= 0 or any(d*(b[key]-stop) <= 0 for b in after):
            return False, 'e3_original_stop_crossed', proof, True
        if d*(quote-trigger['price']) <= 0:
            return False, 'e3_frozen_trigger_not_crossed', proof, False
        return True, 'e3_trigger_ready', proof, False
    except (KeyError, TypeError, ValueError, IndexError, OverflowError):
        return False, 'e3_invalid_evidence', proof, True
