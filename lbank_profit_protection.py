"""Causal advisory protection before TP1; no exchange order or fill claims.

Frozen opt-in policy. Only quotes observed after registration establish MFE.
Original hard stops and confirmed structure failure retain priority. A profit
exit ends this reference, not the market direction or an exchange position.
"""
import copy
import math
from lbank_trend_runner import valid_state, atr_wilder
from lbank_signal_thesis import observed_stop_touch

POLICY = 'native_pre_tp_profit_v1'


def new_policy(direction, entry, stop, fee=.0006, slippage=.0002, funding=.0001):
    values = (entry, stop, fee, slippage, funding)
    if (direction not in (-1, 1) or not all(math.isfinite(float(x)) for x in values)
            or min(entry, stop) <= 0 or not all(0 <= x <= .01 for x in (fee, slippage, funding))):
        raise ValueError('invalid profit protection reference')
    # Solve the same linear cost model as hypothetical accounting, per BTC.
    # effective_exit = quote*(1-d*slippage)
    d = direction
    slope = (d-fee)*(1-d*slippage)
    breakeven = (d*entry+fee*entry+funding*entry)/slope
    cost_distance = d*(breakeven-entry)
    return dict(policy=POLICY, initial_risk=abs(entry-stop),
                arm_distance=max(abs(entry-stop), 2*cost_distance),
                breakeven=breakeven, fee=fee, slippage=slippage, funding=funding)


def enabled(policy):
    return isinstance(policy, dict) and policy.get('policy') == POLICY


def observe(policy, state, *, direction, entry, price, now, native_state=None, minimum_guard=None):
    """Return durable state and a retryable event. No delivery = no acknowledgement.

    Lock 25% of favorable movement beyond modeled break-even, then trail only
    on a new completed 5m bar. Leave at least half an ATR to the current quote.
    A crossed guard is latched even if delivery fails and price rebounds.
    """
    state = copy.deepcopy(state or {})
    if not enabled(policy) or not math.isfinite(price) or price <= 0:
        return state, None
    if now <= state.get('last_at', -1):
        return state, state.get('pending')
    state['last_at'] = now
    d = direction
    if state.get('pending') == 'profit_guard_touched':
        return state, state['pending']
    guard = state.get('guard')
    touch = (observed_stop_touch(native_state,d,guard,state['active_since'],now)
             if guard is not None else None)
    if guard is not None and (d*(price-guard) <= 0 or touch):
        state.update(pending='profit_guard_touched', touch=touch or
                     dict(reason='observed_guard_quote', observed_price=price, observed_at=now))
        return state, state['pending']
    best = state.get('best', entry)
    if d*(price-best)>0: best=price
    state['best'] = best
    if d*(best-entry) < policy['arm_distance']:
        return state, state.get('pending')
    if not valid_state(native_state, now):
        return state, state.get('pending')
    atr = atr_wilder(native_state['five_bars'])
    bar = native_state['last5_start']
    if not atr or atr <= 0:
        return state, state.get('pending')
    # Never activate a newly computed historical guard after it was crossed.
    candidate = policy['breakeven'] + .25*(best-policy['breakeven'])
    if minimum_guard is not None:
        candidate=max(candidate,minimum_guard) if d==1 else min(candidate,minimum_guard)
    if (d*(price-candidate) < .5*atr or candidate <= 0
            or (guard is not None and (state.get('pending') or bar <= state['bar_start']
                or d*(candidate-guard) < max(.25*atr, entry*.0001)))):
        return state, state.get('pending')
    state.update(guard=candidate, active_since=now, bar_start=bar, atr=atr,
                 pending='profit_guard_armed' if guard is None else 'profit_guard_updated')
    return state, state['pending']


def acknowledge(state, reason):
    if state.get('pending') != reason:
        raise ValueError('missing profit protection event')
    state['pending'] = None


def format_notice(state, reason, price, time_kst, reference):
    labels = {'profit_guard_armed':'목표 전 수익 보호 시작',
              'profit_guard_updated':'목표 전 수익 보호선 조정',
              'profit_guard_touched':'수익 보호선 접촉 · 청산 검토'}
    action = ('실제 보유 중이면 청산 여부를 바로 확인하세요.' if reason=='profit_guard_touched'
              else '실제 보유 중이면 거래소 보호 주문을 직접 조정하세요.')
    return (f"[LBank BTCUSDT | {labels[reason]}]\n{reference} | {time_kst} KST\n"
            f"현재 {price:,.1f} | 수익 보호 기준 {state['guard']:,.1f} USDT\n"
            f"{action}\n방향 반전·신규 진입 신호가 아닙니다.\n"
            '실제 체결 미확인·자동주문 없음. 보호선 체결과 순이익은 보장되지 않습니다.')
