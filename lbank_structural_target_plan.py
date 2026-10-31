"""P1 structural targets and inverse entry bound. No I/O or live activation.

All prices are USDT per BTC and quantity is BTC for the linear contract.
Metadata and costs supplied by the caller remain explicit model assumptions.
Receipt-based availability is mandatory unless an offline caller explicitly
opts into a nominal publication scenario. No transformed feed is independent.
"""
import hashlib
import json
import math
from decimal import Decimal, ROUND_FLOOR, ROUND_CEILING

from lbank_entry_quality import confirmed_pivot_barriers
from lbank_native_entry import SOURCE

POLICY = 'structural_target_inverse_entry_p1_v1'


def rounded(value, step, up=False):
    value, step = Decimal(str(value)), Decimal(str(step))
    if not value.is_finite() or not step.is_finite() or step <= 0:
        raise ValueError('invalid tick or quantity step')
    return float((value / step).to_integral_value(
        rounding=ROUND_CEILING if up else ROUND_FLOOR) * step)


def available_barriers(bars, direction, price, now, nominal_delay=None):
    """Return unbreached pivots, each with the right confirmation receipt.

Missing receipt stays null even in a nominal development replay. Reject gaps,
duplicates, forming, invalid OHLC and future observations at this boundary.
"""
    if direction not in (-1, 1) or not math.isfinite(now):
        raise ValueError('invalid direction or clock')
    available = []
    for i, b in enumerate(bars):
        o, h, l, c = [float(b[k]) for k in ('open', 'high', 'low', 'close')]
        end = b['start'] + 300
        receipt = b.get('received_at')
        at = receipt if receipt is not None else (
            end + nominal_delay if nominal_delay is not None else None)
        if (b['start'] % 300 or b['quality'] != 'stream_closed'
                or not all(math.isfinite(x) and x > 0 for x in (o, h, l, c))
                or l > min(o, c) or h < max(o, c) or l > h
                or i and b['start'] - bars[i-1]['start'] != 300):
            raise ValueError('invalid_closed_bar_sequence')
        # A receipt preceding nominal close is a forming update, not proof of
        # final-bar availability. A later receipt cannot be backdated.
        available.append(at is not None and math.isfinite(at) and end <= at <= now)
    result = []
    for pivot in confirmed_pivot_barriers(bars, direction, price):
        i = next(i for i,b in enumerate(bars) if b['start'] == pivot['start'])
        if not all(available[i-2:i+3]):
            continue
        key = 'high' if direction == 1 else 'low'
        if any(available[j] and direction * (b[key] - pivot['price']) >= 0
               for j,b in enumerate(bars) if j >= i+3):
            continue
        support = bars[i-2:i+3]
        receipts = [b.get('received_at') for b in support]
        known = max(receipts) if all(x is not None for x in receipts) else None
        result.append(dict(pivot, known_at=known,
            available_at=max(b.get('received_at') if b.get('received_at') is not None
                             else b['start']+300+nominal_delay for b in support),
            right_confirmation_start=bars[i+2]['start'],
            availability_basis='observed_receipt' if known is not None else 'nominal_assumption',
            source_sha256=hashlib.sha256(json.dumps(support, sort_keys=True,
                separators=(',', ':'), allow_nan=False).encode()).hexdigest()))
    return sorted(result, key=lambda b:direction*b['price'])


def entry_bound(direction, stop, target, pnl, ratio):
    """Most adverse permissible entry, holding structural S/T fixed.

The supplied model must be monotonic for linear-contract fees/slippage/funding.
We solve net_gain(E,T) - ratio * net_loss(E,S) = 0, rather than manufacture T.
"""
    if (direction not in (-1, 1) or not all(math.isfinite(x) and x > 0
            for x in (stop, target, ratio)) or direction*(target-stop) <= 0):
        raise ValueError('invalid structural geometry')
    def surplus(entry):
        value = pnl(direction, entry, target, exit_kind='take_profit') + ratio*pnl(direction, entry, stop)
        if not math.isfinite(value):
            raise ValueError('invalid cost model output')
        return value
    good, bad = stop, target
    if surplus(good) <= 0:
        return None
    for _ in range(70):
        mid = (good+bad)/2
        if surplus(mid) >= 0:
            good = mid
        else:
            bad = mid
    return good


class StructuralTargetPolicy:
    def __init__(self, *, now, pnl, ratio, tick, quantity_step, minimum_quantity,
                 minimum_notional, nominal_delay=None, metadata_basis='assumed_public_metadata'):
        if (not all(math.isfinite(x) and x > 0 for x in
                    (now, ratio, tick, quantity_step, minimum_quantity, minimum_notional))
                or nominal_delay is not None and (not math.isfinite(nominal_delay) or nominal_delay < 0)):
            raise ValueError('invalid model/metadata')
        self.now, self.pnl, self.ratio = now, pnl, ratio
        self.tick, self.step = tick, quantity_step
        self.minimum_quantity, self.minimum_notional = minimum_quantity, minimum_notional
        self.nominal_delay, self.metadata_basis = nominal_delay, metadata_basis
        self.last = None

    def quantity(self, maximum, price):
        q = rounded(maximum, self.step)
        return q if q >= self.minimum_quantity and q*price >= self.minimum_notional else 0.

    def __call__(self, direction, entry, stop, setup, state):
        result = dict(policy=POLICY, status='room_unknown', stop=stop,
                      metadata_basis=self.metadata_basis, now=self.now)
        self.last = result
        try:
            if (state is None or state['source'] != SOURCE or direction not in (-1,1)
                    or not all(math.isfinite(v) and v > 0 for v in (entry, stop))):
                raise ValueError('invalid_native_input')
            frozen = setup.get('structural_target_plan')
            if frozen:
                # A candidate's structure cannot widen its stop or extend its
                # target at confirmation to rescue an expensive late entry.
                stop = frozen['stop']; barrier = dict(frozen['barrier'])
                if (frozen['policy'] != POLICY or not math.isfinite(barrier['available_at'])
                        or barrier['available_at'] > self.now):
                    raise ValueError('future_barrier')
                target = frozen['target1']
                if direction*(barrier['price']-target) < self.tick-1e-8:
                    raise ValueError('target_past_frozen_barrier')
                current = available_barriers(state['five_bars'], direction,
                    entry, self.now, self.nominal_delay)
                if not any(b['start'] == barrier['start'] and b['price'] == barrier['price']
                           and b['source_sha256'] == barrier['source_sha256'] for b in current):
                    result['status'] = 'structural_barrier_invalidated'
                    return result
                if direction*(current[0]['price']-target) < self.tick-1e-8:
                    result['status'] = 'room_conflict'
                    return result
            else:
                barriers = available_barriers(state['five_bars'], direction,
                    entry, self.now, self.nominal_delay)
                if not barriers:
                    return result
                barrier = barriers[0]
                # Preserve the existing 1.5bp pre-barrier buffer, with at least
                # one tick; round toward entry, never through the barrier.
                target = rounded(barrier['price'] - direction*max(self.tick, entry*.00015),
                                 self.tick, up=direction == -1)
            # A nearer already observed setup barrier also bounds the target.
            if setup.get('barrier') is not None and direction*(setup['barrier']-target) < max(self.tick,entry*.00015):
                result['status'] = 'room_conflict'
                return result
            stop = rounded(stop, self.tick, up=direction == -1)
            bound = entry_bound(direction, stop, target, self.pnl, self.ratio)
            result.update(stop=stop, target1=target, target2=target, barrier=barrier,
                maximum_permissible_entry=bound, bound_kind='maximum_long' if direction == 1 else 'minimum_short',
                target1_basis='nearest_observed_unbreached_pivot_before_buffer',
                exit_assumption='both benchmark halves at same structural T1; no invented T2',
                stop_basis='existing_structural_stop_rounded_outward_and_frozen_at_candidate')
            loss = -self.pnl(direction, entry, stop)
            gain = self.pnl(direction, entry, target, exit_kind='take_profit')
            result.update(net_unit_loss=loss, net_unit_gain=gain,
                          net_rr=gain/loss if loss > 0 else None)
            result['status'] = ('qualified' if bound is not None and loss > 0
                and direction*(entry-stop) > 0 and direction*(target-entry) > 0
                and gain >= self.ratio*loss-1e-8 else 'room_insufficient')
            return result
        except (KeyError, TypeError, ValueError, IndexError, OverflowError):
            result['status'] = 'invalid_structural_evidence'
            return result
