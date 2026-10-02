import pytest
from trade_flow import TradeFlow, buying_persistent


def strong_flow(now, final=101):
    flow = TradeFlow()
    for index in range(61):
        price = final * (1 - .002 + min(index, 59) / 59 * .002)
        flow.observe('KRW-TEST', price, 2000, int((now - 60 + index) * 1000),
                     'ASK' if index % 3 == 0 else 'BID', index)
    return flow.snapshot('KRW-TEST', int(now))


def test_completed_seconds_buy_notional_is_not_waiting_book_or_current_second():
    flow = TradeFlow()
    now = 1800000100
    for index in range(61):
        price = 100 + index * .004
        flow.observe('KRW-TEST', price, 2000, (now - 60 + index) * 1000,
                     'ASK' if index % 3 == 0 else 'BID', index)
    snapshot = flow.snapshot('KRW-TEST', now)
    assert snapshot['trade_count'] == 60
    assert snapshot['buy_share_pct'] > 60
    assert buying_persistent(snapshot, 100.24, now)
    # Current second is never counted even if dominated by large sells.
    flow.observe('KRW-TEST', 100.24, 1e9, now * 1000, 'ASK', 99)
    assert flow.snapshot('KRW-TEST', now) == snapshot
    flow.clear()
    assert not flow.snapshot('KRW-TEST', now)['ready']


@pytest.mark.parametrize('cause', ['stale', 'startup', 'sell', 'latest_sell', 'falling_low',
                                    'low_value', 'thin_window', 'falling_price', 'chase', 'unknown', 'gap'])
def test_flow_does_not_certify_incomplete_or_unsafe_evidence(cause):
    now = 1800000100
    context = strong_flow(now)
    price = 101
    if cause == 'stale': context['as_of'] -= 11
    elif cause == 'startup': context['ready'] = False
    elif cause == 'sell':
        for w in context['windows']: w['buy_krw'], w['sell_krw'] = w['sell_krw'], w['buy_krw']
    elif cause == 'latest_sell':
        w = context['windows'][-1]; w['buy_krw'] = 0
    elif cause == 'falling_low': context['windows'][-1]['low'] = 99
    elif cause == 'low_value':
        for w in context['windows']: w['buy_krw'] /= 100; w['sell_krw'] /= 100
    elif cause == 'thin_window': context['windows'][1]['count'] = 4
    elif cause == 'falling_price': context['windows'][-1]['close'] = 100
    elif cause == 'chase': price = 103
    elif cause == 'unknown': context['source'] = 'warm_ohlc'
    elif cause == 'gap': context['windows'][1]['count'] = 0
    assert not buying_persistent(context, price, now)


def test_duplicate_unknown_and_out_of_order_ticks_do_not_inflate_flow():
    flow = TradeFlow()
    flow.observe('X', 100, 2000, 100000, 'BID', 1)
    flow.observe('X', 100, 2000, 100000, 'BID', 1)
    flow.observe('X', 100, 2000, 99000, 'BID', 2)
    assert flow.snapshot('X', 101)['trade_count'] == 1
    flow.observe('X', 100, 2000, 100100, None, 3)
    assert not flow.snapshot('X', 161)['ready']


def test_bucket_overflow_is_bounded_and_cannot_be_certified():
    flow = TradeFlow()
    for index in range(600): flow.observe('X', 100, 2000, 100000, 'BID', index)
    assert len(flow._buckets['X'][-1].ids) == 512
    assert flow._buckets['X'][-1].incomplete
