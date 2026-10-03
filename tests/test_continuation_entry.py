"""Completed public-price structure, admission and fresh survival regression."""
import copy
import json
from datetime import datetime
from pathlib import Path

import pytest
import candidate_analysis as ca
from test_candidate_analysis import _flow_setup, _evaluate_flow, _config, _five_wb_retest_setup, _evaluate_hot
from trade_flow import buying_persistent

REAL_WB_CONTEXT = ca._double_bollinger_context


def _public_case(name):
    return json.loads((Path(__file__).parent / 'fixtures/wb-continuation-20261003.json').read_text())[name]


def _context(case):
    return ca._completed_leader_context(case['minute1'], case['minute5'], case['minute15'],
        datetime.fromisoformat(case['as_of'].replace('Z', '+00:00')))


def test_actual_sui_completed_recovery_is_not_a_second_breakout_requirement():
    case = _public_case('sui')
    proof = _context(case)
    assert proof['confirmed'] and proof['level'] == 1567
    assert proof['close'] == 1585 and proof['low'] == 1568
    assert not proof['retest']  # This route never fabricates an anchor touch.
    assert proof['volume_5m'] >= 1.5 and proof['volume_15m'] >= 1
    case['minute5'][0].update(trade_price=99999, high_price=99999, low_price=1,
                             candle_acc_trade_volume=1e12)
    assert _context(case) == proof  # Progress candle must not help OR veto.


@pytest.mark.parametrize('cause', ['five_volume', 'fifteen_volume', 'gap', 'falling_low',
    'falling_close', 'wick', 'support_break', 'no_wb', 'fifteen_falling'])
def test_real_continuation_requires_completed_volume_and_unbroken_structure(cause):
    case = _public_case('sui')
    five, fifteen = case['minute5'], case['minute15']
    if cause == 'five_volume': five[1]['candle_acc_trade_volume'] = 1
    elif cause == 'fifteen_volume': fifteen[1]['candle_acc_trade_volume'] = 1
    elif cause == 'gap': case['minute1'].pop(5)
    elif cause == 'falling_low': five[1]['low_price'] = five[2]['low_price'] - 1
    elif cause == 'falling_close': five[1]['trade_price'] = five[2]['trade_price'] - 1
    elif cause == 'wick': five[1]['high_price'] = 1700
    elif cause == 'support_break': five[2]['trade_price'] = 1500
    elif cause == 'no_wb':
        for b in five: b.update(opening_price=100, high_price=101, low_price=99, trade_price=100)
    elif cause == 'fifteen_falling': fifteen[1]['trade_price'] = 1500
    assert not _context(case)['confirmed'], cause


def _continuation_fixture(monkeypatch):
    fixture = _flow_setup(monkeypatch)
    monkeypatch.setattr(ca, '_double_bollinger_context', REAL_WB_CONTEXT)
    setup, hourly, _ = fixture
    now, one, five, fifteen, btc, alert, ticker = setup
    five[4].update(opening_price=100, high_price=100.6, low_price=100,
                   trade_price=100.5, candle_acc_trade_volume=500)
    for i, low, close in [(3, 100.3, 100.7), (2, 100.5, 100.9), (1, 100.7, 101.1)]:
        five[i].update(opening_price=low, low_price=low, high_price=close + .05,
                       trade_price=close, candle_acc_trade_volume=400)
    ticker.update(trade_price=101.1, high_price=101.15)
    alert.update(price=101.1, breakout_level=100.2, relative_strength_eligible=False)
    hourly[1]['trade_price'] = 100.05  # Below the 1h MA; recovery is not established.
    return fixture


def test_recovery_flow_can_confirm_rising_completed_structure_before_hourly_ma_turn(monkeypatch):
    fixture = _continuation_fixture(monkeypatch)
    candidate, reasons = _evaluate_flow(fixture)
    assert candidate is not None, reasons
    assert candidate['flow_continuation_entry'] and candidate['trade_flow_leader']
    assert candidate['double_bb_timeframe'] == '5분'
    assert not candidate['double_bb_true_breakout']
    assert not candidate['higher_timeframe_context']['hourly_established']
    assert candidate['first_target_risk_reward'] >= 2
    assert candidate['stop_timeframe'] == '5분 구조'
    setup, hourly, books = fixture
    now, one, five, fifteen, _, alert, ticker = setup
    update, reasons = ca.validate_candidate_survival(candidate, ticker, books, one, _config(),
        as_of=now, candles_5m=five, candles_15m=fifteen, candles_60m=hourly,
        trade_flow_context=alert['trade_flow_context'])
    assert reasons == []
    assert update['survival_continuation_context']['confirmed']


@pytest.mark.parametrize('cause', ['selling', 'stale', 'one_book', 'thin_book', 'spread',
    'rank', 'breadth', 'five_volume', 'fifteen_volume', 'gap', 'chase', 'btc',
    'rsi_extreme', 'support_lost', 'liquidity', 'disabled'])
def test_new_recovery_route_keeps_risk_and_execution_floors(monkeypatch, cause):
    fixture = _continuation_fixture(monkeypatch)
    setup, _, books = fixture
    now, one, five, fifteen, btc, alert, ticker = setup
    config = {}
    if cause == 'selling':
        for w in alert['trade_flow_context']['windows']: w['buy_krw'] = 0
    elif cause == 'stale': alert['trade_flow_context']['as_of'] -= 11
    elif cause == 'one_book': books[:] = books[:1]
    elif cause == 'thin_book':
        for u in books[0]['orderbook_units']: u['bid_size'] = 1
    elif cause == 'spread': books[0]['orderbook_units'][0]['ask_price'] = 103
    elif cause == 'rank': alert['relative_strength_percentile'] = 3
    elif cause == 'breadth': alert['market_breadth_5m_pct'] = 19
    elif cause == 'five_volume': five[1]['candle_acc_trade_volume'] = 1
    elif cause == 'fifteen_volume': fifteen[1]['candle_acc_trade_volume'] = 1
    elif cause == 'gap': one.pop(5)
    elif cause == 'chase': ticker['trade_price'] = 104
    elif cause == 'btc': btc[1]['trade_price'] = 98
    elif cause == 'rsi_extreme': monkeypatch.setattr(ca, 'analyze_candles',
        lambda c: {**__import__('analysis').analyze_candles(c), 'rsi14': 95})
    elif cause == 'support_lost': one[1]['trade_price'] = 100.4
    elif cause == 'liquidity': ticker['acc_trade_price_24h'] = 1
    elif cause == 'disabled': config['trade_flow_leader_enabled'] = False
    candidate, reasons = _evaluate_flow(fixture, **config)
    assert candidate is None, (cause, candidate)
    assert reasons


def test_flat_sixty_second_price_requires_independent_completed_trend_evidence():
    flow = {'ready': True, 'source': 'upbit_websocket_trade', 'window_seconds': 60, 'as_of': 123,
            'windows': [{'buy_krw': 4e6, 'sell_krw': 1e6, 'count': 10, 'low': 100, 'close': 100}] * 3}
    assert not buying_persistent(flow, 100, 123)
    assert buying_persistent(flow, 100, 123, min_close_gain_pct=0)
    flow['windows'][-1] = {**flow['windows'][-1], 'close': 99.99}
    assert not buying_persistent(flow, 100, 123, min_close_gain_pct=0)


def test_wb_first_retest_does_not_require_a_second_explosive_pattern(monkeypatch):
    fixture = _five_wb_retest_setup(monkeypatch)
    monkeypatch.setattr(ca, '_explosive_bar_context', lambda *a, **k: {'confirmed': False})
    candidate, reasons = _evaluate_hot(fixture)
    assert candidate is not None, reasons
    assert candidate['completed_wb_retest_entry']


@pytest.mark.parametrize('cause', ['volume', 'selling', 'stale', 'support', 'heat'])
def test_continuation_survival_requires_fresh_volume_flow_support_and_heat(monkeypatch, cause):
    fixture = _continuation_fixture(monkeypatch)
    candidate, reasons = _evaluate_flow(fixture)
    assert candidate is not None, reasons
    setup, hourly, books = fixture
    now, one, five, fifteen, _, alert, ticker = setup
    if cause == 'volume': five[1]['candle_acc_trade_volume'] = 1
    elif cause == 'selling':
        for w in alert['trade_flow_context']['windows']: w['buy_krw'] = 0
    elif cause == 'stale': alert['trade_flow_context']['as_of'] -= 11
    elif cause == 'support': one[1]['trade_price'] = 100.4
    elif cause == 'heat': monkeypatch.setattr(ca, 'analyze_candles',
        lambda c: {**__import__('analysis').analyze_candles(c), 'rsi14': 95})
    _, reasons = ca.validate_candidate_survival(candidate, ticker, books, one, _config(),
        as_of=now, candles_5m=five, candles_15m=fifteen, candles_60m=hourly,
        trade_flow_context=alert['trade_flow_context'])
    assert any('체결 매수' in r for r in reasons), reasons


def test_continuation_does_not_erase_unbroken_resistance(monkeypatch):
    fixture = _continuation_fixture(monkeypatch)
    monkeypatch.setattr(ca, '_resistances', lambda *a, **k: [101.2])
    candidate, reasons = _evaluate_flow(fixture)
    assert candidate is None
    assert any('저항' in r for r in reasons)


def test_wide_initial_continuation_stop_is_rejected_not_clipped(monkeypatch):
    fixture = _continuation_fixture(monkeypatch)
    # A large recent true range affects a new structural stop independently
    # of intact current price support; never force it inside the loss cap.
    monkeypatch.setattr(ca, '_atr', lambda bars: 4.0)
    candidate, reasons = _evaluate_flow(fixture)
    assert candidate is None
    assert any('구조 손절폭' in r for r in reasons), reasons


def test_real_pod_structure_does_not_certify_buy_flow_or_a_safe_stop():
    case = _public_case('pod')
    proof = _context(case)
    assert proof['confirmed']
    assert (proof['close'] / proof['low'] - 1) * 100 > 3
    flow = {'ready': True, 'source': 'upbit_websocket_trade', 'window_seconds': 60, 'as_of': 123,
            'windows': [{'buy_krw': 1e6, 'sell_krw': 4e6, 'count': 10, 'low': 936, 'close': 936}] * 3}
    assert not buying_persistent(flow, 936, 123, min_close_gain_pct=0)
