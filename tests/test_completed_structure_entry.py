"""Causal public-bar replays plus independent execution/dispatch regressions."""
import asyncio
import json
from datetime import datetime
from pathlib import Path

import pytest
import candidate_analysis as ca
import monitor
from test_candidate_analysis import _config, _flow_setup, _evaluate_flow
from test_monitor import _flow_telegram_candidate

REAL_WB = ca._double_bollinger_context


def public_case(market, clock):
    data = json.loads((Path(__file__).parent / 'fixtures/axs-blast-completed-20261004.json').read_text())
    case = data['cases'][market + '_' + clock]
    return datetime.fromisoformat(case['as_of']), case['bars']


@pytest.mark.parametrize('market,clock,confirmed,mode', [
    ('KRW-AXS', '04:10', True, 'completed_rolling_breakout'),
    ('KRW-AXS', '03:20', False, 'completed_rolling_breakout'),
    ('KRW-BLAST', '00:05', True, 'standard'),
    ('KRW-BLAST', '00:10', False, 'standard'),
    ('KRW-BLAST', '00:25', True, 'standard'),
])
def test_public_replay_distinguishes_new_breakout_from_failed_first_dip(market, clock, confirmed, mode):
    now, bars = public_case(market, clock)
    proof = ca._completed_structure_context(bars['minute1'], bars['minute5'], bars['minute15'], now, _config())
    assert bool(proof['confirmed']) == confirmed
    assert proof['volume_mode'] == mode
    if market == 'KRW-AXS' and clock == '04:10':
        assert proof['volume_5m'] == pytest.approx(5.16786, rel=.0001)
        assert proof['volume_15m'] == pytest.approx(.63101, rel=.0001)
        assert proof['rolling_15m_volume_ratio'] == pytest.approx(2.30603, rel=.0001)
        assert .65 < proof['close_position'] < .75
        assert proof['level'] == 1807
    if confirmed:
        assert not proof['retest']
    # A forming bar can neither enable nor veto the completed decision.
    for iv in ('minute1', 'minute5', 'minute15'):
        bars[iv].insert(0, {**bars[iv][0], 'candle_date_time_utc': now.isoformat(),
            'trade_price': 9999, 'high_price': 9999, 'low_price': 1, 'candle_acc_trade_volume': 1e15})
    assert ca._completed_structure_context(bars['minute1'], bars['minute5'], bars['minute15'], now, _config()) == proof


@pytest.mark.parametrize('cause', ['history', 'gap5', 'gap1', 'volume5', 'rolling', 'wick', 'lost_level'])
def test_public_rolling_alternative_fails_closed(cause):
    now, bars = public_case('KRW-AXS', '04:10')
    if cause == 'history': bars['minute5'] = bars['minute5'][:62]
    elif cause == 'gap5': bars['minute5'].pop(40)
    elif cause == 'gap1': bars['minute1'].pop(5)
    elif cause == 'volume5': bars['minute5'][0]['candle_acc_trade_volume'] = 1
    elif cause == 'rolling':
        for b in bars['minute5'][3:]: b['candle_acc_trade_volume'] = 1e12
    elif cause == 'wick': bars['minute5'][0]['high_price'] = 2100
    elif cause == 'lost_level': bars['minute5'][0]['trade_price'] = 1800
    proof = ca._completed_structure_context(bars['minute1'], bars['minute5'], bars['minute15'], now, _config())
    assert not proof['confirmed']


def structure_fixture(monkeypatch):
    fixture = _flow_setup(monkeypatch)
    monkeypatch.setattr(ca, '_double_bollinger_context', REAL_WB)
    setup, _, books = fixture
    setup[5].pop('trade_flow_context')  # No fabricated executed-buying proof.
    setup[3][1]['candle_acc_trade_volume'] = 50
    for book in books:
        book.update(total_ask_size=1000000, total_bid_size=400000)
        for unit in book['orderbook_units']:
            unit.update(bid_size=100000, ask_size=80000)
    return fixture


def survive(candidate, fixture):
    setup, hourly, books = fixture
    now, one, five, fifteen, _, alert, ticker = setup
    return ca.validate_candidate_survival(candidate, ticker, books, one,
        _config(completed_structure_enabled=True), as_of=now, candles_5m=five,
        candles_15m=fifteen, candles_60m=hourly, trade_flow_context=alert.get('trade_flow_context'))


def delivery_candidate(monkeypatch, fixture):
    c, reasons = _evaluate_flow(fixture, completed_structure_enabled=True)
    assert c is not None, reasons
    update, reasons = survive(c, fixture)
    assert not reasons
    c.update(update)
    now = fixture[0][0].timestamp()
    monkeypatch.setattr(monitor.time, 'time', lambda: now)
    monkeypatch.setenv('CANDIDATE_COMPLETED_STRUCTURE_ENABLED', 'true')
    template = _flow_telegram_candidate(now)
    for k in ('survival_confirmed', 'survival_seconds', 'survival_btc_5m_pct',
              'survival_btc_15m_pct', 'survival_btc_live_pct'):
        c[k] = template[k]
    c['dispatch_execution_quote'] = {**ca._quote_execution_context(fixture[2][-1], c['current_price'], _config()), 'as_of': now}
    return c


def test_new_route_preserves_five_minute_structure_without_claiming_retest_or_buy_flow(monkeypatch):
    fixture = structure_fixture(monkeypatch)
    c = delivery_candidate(monkeypatch, fixture)
    assert c['completed_structure_entry'] and not c['trade_flow_leader']
    assert not c['first_retest_confirmed']
    assert c['volume_timeframe'] == '5분' and c['double_bb_timeframe'] == '5분'
    assert c['orderbook_bid_ask_ratio'] < .5
    assert c['completed_15m_volume_ratio'] < 1
    assert c['execution_cost']['net_risk_reward'] >= 1
    assert not monitor.AlertDispatcher().candidate_delivery_reasons(c)
    text = monitor._candidate_text(c)
    assert '완료 5분 구조 돌파형' in text
    assert '연속 63봉' in text and '실제 매수 체결 우위 미확인' in text
    assert '실제 체결:' not in text and '첫 눌림·돌파선 재지지 확인' not in text


@pytest.mark.parametrize('cause', ['no_structure', 'selling', 'thin', 'far_only', 'one_book', 'wide',
    'rank', 'breadth', 'rsi', 'btc', 'below_anchor', 'chase', 'liquidity', 'risk', 'disabled'])
def test_new_route_does_not_replace_execution_or_risk_evidence(monkeypatch, cause):
    fixture = structure_fixture(monkeypatch)
    now, one, five, fifteen, btc, alert, ticker = fixture[0]
    cfg = {'completed_structure_enabled': True}
    if cause == 'no_structure': five[1]['trade_price'] = 100.1
    elif cause == 'selling': alert['trade_flow_context'] = {'ready': True, 'as_of': now.timestamp(),
        'windows': [{'buy_krw': 0, 'sell_krw': 1e8}]}
    elif cause == 'thin':
        for u in fixture[2][0]['orderbook_units']: u['bid_size'] = 1
    elif cause == 'far_only':
        for u in fixture[2][0]['orderbook_units']: u['bid_price'] = 90
    elif cause == 'one_book': fixture[2][:] = fixture[2][:1]
    elif cause == 'wide': fixture[2][0]['orderbook_units'][0]['ask_price'] = 103
    elif cause == 'rank': alert['relative_strength_percentile'] = 3
    elif cause == 'breadth': alert['market_breadth_5m_pct'] = 19
    elif cause == 'rsi': monkeypatch.setattr(ca, 'analyze_candles', lambda c: {**__import__('analysis').analyze_candles(c), 'rsi14': 95})
    elif cause == 'btc': btc[1]['trade_price'] = 98
    elif cause == 'below_anchor': one[1]['trade_price'] = 100
    elif cause == 'chase': ticker['trade_price'] = 104
    elif cause == 'liquidity': ticker['acc_trade_price_24h'] = 1
    elif cause == 'risk': cfg['max_stop_loss_pct'] = .2
    elif cause == 'disabled': cfg['completed_structure_enabled'] = False
    c, reasons = _evaluate_flow(fixture, **cfg)
    assert c is None, (cause, c)
    assert reasons


@pytest.mark.parametrize('cause', ['volume', 'book', 'anchor', 'trend', 'selling'])
def test_survival_recomputes_structure_and_nearby_book(monkeypatch, cause):
    fixture = structure_fixture(monkeypatch)
    c, reasons = _evaluate_flow(fixture, completed_structure_enabled=True)
    assert c is not None, reasons
    now, one, five, _, _, alert, _ = fixture[0]
    if cause == 'volume': five[1]['candle_acc_trade_volume'] = 1
    elif cause == 'book':
        for u in fixture[2][0]['orderbook_units']: u['bid_size'] = 1
    elif cause == 'anchor': one[1]['trade_price'] = 100
    elif cause == 'trend': fixture[1][1]['trade_price'] = 90
    elif cause == 'selling': alert['trade_flow_context'] = {'ready': True, 'as_of': now.timestamp(),
        'windows': [{'buy_krw': 0, 'sell_krw': 1e8}]}
    _, reasons = survive(c, fixture)
    assert any('구조·거래량' in r for r in reasons)


@pytest.mark.parametrize('cause', ['stale', 'missing', 'volume', 'gap', 'book', 'quote', 'btc', 'risk', 'cost', 'wait'])
def test_dispatch_rejects_stale_or_invalid_structure_evidence(monkeypatch, cause):
    c = delivery_candidate(monkeypatch, structure_fixture(monkeypatch))
    assert not monitor.AlertDispatcher().candidate_delivery_reasons(c)
    if cause == 'stale': c['survival_structure_context']['as_of'] -= 11
    elif cause == 'missing': c.pop('survival_structure_context')
    elif cause == 'volume': c['survival_structure_context']['rolling_15m_volume_ratio'] = .99
    elif cause == 'gap': c['survival_structure_context']['rolling_history_contiguous'] = False
    elif cause == 'book': c['survival_structure_book_context']['confirmed'] = False
    elif cause == 'quote': c['dispatch_execution_quote']['as_of'] -= 11
    elif cause == 'btc': c['survival_btc_5m_pct'] = -1
    elif cause == 'risk': c['stop_price'] = 95
    elif cause == 'cost': c['execution_cost_buffer_pct'] = 10
    elif cause == 'wait': c['survival_seconds'] = 59
    assert monitor.AlertDispatcher().candidate_delivery_reasons(c)


def test_broken_initial_pullback_allows_only_new_high_completed_breakout_screen(monkeypatch):
    monkeypatch.setenv('ENABLE_CANDIDATE_ANALYSIS', 'true')
    monkeypatch.setenv('CANDIDATE_COMPLETED_STRUCTURE_ENABLED', 'true')
    monkeypatch.setattr(monitor.MONITOR_STATE, 'has_recent_signal', lambda *a: False)
    relative = {'relative_strength_ready': True, 'relative_strength_eligible': True,
        'relative_strength_percentile': 1, 'momentum_5m_pct': 2, 'momentum_15m_pct': 3, 'momentum_60m_pct': 5}
    analyzer = monitor.CandidateAnalyzer(ca.CandidateConfig.from_env(), monitor.AlertDispatcher(), lambda *a: relative)
    analyzer._watchlist['KRW-TEST'] = {'expires_at': 1800010000, 'source_signal': 'breakout',
        'first_signal_price': 102, 'breakout_level': 100, 'leader_peak_price': 105,
        'leader_pullback_invalidated': True}
    seen = []
    async def analyze(alert): seen.append(alert)
    monkeypatch.setattr(analyzer, '_analyze', analyze)
    async def run():
        assert not analyzer._observe_completed_bar_watchlist('KRW-TEST', 104, 1800000305)
        assert analyzer._observe_completed_bar_watchlist('KRW-TEST', 106, 1800000305)
        assert not analyzer._observe_completed_bar_watchlist('KRW-TEST', 106, 1800000306)
        await asyncio.sleep(0)
        await asyncio.sleep(0)
    asyncio.run(run())
    assert len(seen) == 1 and seen[0]['completed_bar_recheck']
    assert not seen[0].get('is_reentry') and not seen[0].get('pullback_retest')
    assert seen[0]['fresh_breakout_recheck']
    assert seen[0]['original_signal_time_utc'] == seen[0]['time_utc']
    assert analyzer._watchlist['KRW-TEST']['leader_pullback_invalidated']


def test_mocked_telegram_send_uses_new_route_and_refresh_quote(monkeypatch):
    candidate = delivery_candidate(monkeypatch, structure_fixture(monkeypatch))
    monkeypatch.setenv('TELEGRAM_BOT_TOKEN', 'test-token')
    monkeypatch.setenv('TELEGRAM_CHAT_ID', 'test-chat')
    sent = []
    class Client:
        def __init__(self, *a, **k): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *a): pass
        async def post(self, url, json):
            sent.append(json['text'])
            class Response:
                def raise_for_status(self): pass
            return Response()
    monkeypatch.setattr(monitor.httpx, 'AsyncClient', Client)
    assert asyncio.run(monitor.AlertDispatcher().send_candidate(candidate))
    assert len(sent) == 1 and '완료 5분 구조 돌파형' in sent[0]
    assert '실제 체결:' not in sent[0]
