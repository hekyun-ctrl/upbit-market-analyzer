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


def test_fresh_rebreakout_accepts_leadership_instead_of_duplicate_hourly_gates(monkeypatch):
    fixture = structure_fixture(monkeypatch)
    alert = fixture[0][5]
    alert.update(fresh_breakout_recheck=True, origin_signal_price=100,
                 relative_strength_eligible=False, momentum_60m_pct=0)
    # Keep the top-ranked 5m/15m leader evidence, while the mature hourly
    # trend is not yet aligned. The fresh setup may use either proof.
    fixture[1][1]['trade_price'] = 90
    candidate, reasons = _evaluate_flow(fixture, completed_structure_enabled=True)
    assert candidate is not None, reasons
    assert candidate['fresh_breakout_recheck']
    assert candidate['structure_confirmation_basis'] == 'relative_leadership'
    _, survival_reasons = survive(candidate, fixture)
    assert survival_reasons == []


def test_completed_structure_requires_two_of_four_quality_proofs(monkeypatch):
    higher_trend = structure_fixture(monkeypatch)
    higher_trend[0][5].update(fresh_breakout_recheck=True, origin_signal_price=100,
                              relative_strength_percentile=6,
                              relative_strength_eligible=False, momentum_60m_pct=0)
    candidate, reasons = _evaluate_flow(higher_trend, completed_structure_enabled=True)
    assert candidate is not None, reasons
    assert sum(candidate['completed_structure_quality_checks'].values()) >= 2

    unsupported = structure_fixture(monkeypatch)
    unsupported[0][5].update(fresh_breakout_recheck=True, origin_signal_price=100,
                             relative_strength_percentile=6, relative_strength_eligible=False,
                             momentum_5m_pct=-1.0, momentum_15m_pct=-1.0,
                             momentum_60m_pct=0, market_breadth_5m_pct=0)
    unsupported[1][1]['trade_price'] = 90
    candidate, reasons = _evaluate_flow(unsupported, completed_structure_enabled=True)
    assert candidate is None
    assert any('구조 보강 근거 2/4' in reason for reason in reasons)


def test_fresh_setup_reanchors_and_treats_ordinary_breadth_as_quality_not_veto(monkeypatch):
    fixture = structure_fixture(monkeypatch)
    now, one, five, fifteen, _, alert, ticker = fixture[0]
    origin = float(ticker['trade_price']) * .90
    alert.update(fresh_breakout_recheck=True, origin_signal_price=origin,
                 relative_strength_percentile=3.0, relative_strength_eligible=False,
                 momentum_60m_pct=0.0, market_breadth_5m_pct=17.0)
    # The individual coin's completed 5m structure remains intact while the
    # old impulse is >5% behind and the mature hourly trend still lags.
    fixture[1][1]['trade_price'] = 90
    candidate, reasons = _evaluate_flow(fixture, completed_structure_enabled=True)
    assert candidate is not None, reasons
    assert candidate['origin_extension_pct'] > 5
    assert candidate['setup_anchor_reason'] == 'completed_5m_structure_close'
    assert candidate['setup_extension_pct'] <= 1.0
    assert candidate['setup_score_floor'] <= 80
    _, survival_reasons = survive(candidate, fixture)
    assert survival_reasons == []
    analyzer = monitor.CandidateAnalyzer(_config(completed_structure_enabled=True),
                                         monitor.AlertDispatcher())
    relative = {'relative_strength_ready': True, 'relative_strength_percentile': 3.0,
        'momentum_5m_pct': .5, 'momentum_15m_pct': .3, 'market_breadth_5m_pct': 17.0}
    assert analyzer._relative_survival_rejections(candidate, relative) == []


def test_ordinary_structure_route_accepts_alternative_quality_proofs(monkeypatch):
    fixture = structure_fixture(monkeypatch)
    fixture[0][5].update(momentum_60m_pct=0, relative_strength_percentile=3,
                          relative_strength_eligible=False)
    fixture[1][1]['trade_price'] = 90
    candidate, reasons = _evaluate_flow(fixture, completed_structure_enabled=True)
    assert candidate is not None, reasons
    assert sum(candidate['completed_structure_quality_checks'].values()) >= 2
    _, survival_reasons = survive(candidate, fixture)
    assert survival_reasons == []


@pytest.mark.parametrize('cause', ['no_structure', 'selling', 'thin', 'far_only', 'one_book', 'wide',
    'quality', 'rsi', 'btc', 'below_anchor', 'chase', 'liquidity', 'risk', 'disabled'])
def test_new_route_does_not_replace_execution_or_risk_evidence(monkeypatch, cause):
    fixture = structure_fixture(monkeypatch)
    now, one, five, fifteen, btc, alert, ticker = fixture[0]
    cfg = {'completed_structure_enabled': True}
    if cause == 'no_structure': five[1]['trade_price'] = 100.1
    elif cause == 'selling': alert['trade_flow_context'] = {'ready': True, 'as_of': now.timestamp(),
        'windows': [{'buy_krw': 0, 'sell_krw': 1e8}]}
    elif cause == 'thin':
        for book in fixture[2]:
            for u in book['orderbook_units']: u['bid_size'] = 1
    elif cause == 'far_only':
        for book in fixture[2]:
            for u in book['orderbook_units']: u['bid_price'] = 90
    elif cause == 'one_book': fixture[2][:] = fixture[2][:1]
    elif cause == 'wide':
        for book in fixture[2]: book['orderbook_units'][0]['ask_price'] = 103
    elif cause == 'quality':
        alert.update(relative_strength_percentile=6, relative_strength_eligible=False,
                     momentum_5m_pct=-1, momentum_15m_pct=-1, market_breadth_5m_pct=0)
        fixture[1][1]['trade_price'] = 90
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


@pytest.mark.parametrize('cause', ['volume', 'book', 'anchor', 'quality', 'selling'])
def test_survival_recomputes_structure_and_nearby_book(monkeypatch, cause):
    fixture = structure_fixture(monkeypatch)
    c, reasons = _evaluate_flow(fixture, completed_structure_enabled=True)
    assert c is not None, reasons
    now, one, five, _, _, alert, _ = fixture[0]
    if cause == 'volume': five[1]['candle_acc_trade_volume'] = 1
    elif cause == 'book':
        for book in fixture[2]:
            for u in book['orderbook_units']: u['bid_size'] = 1
    elif cause == 'anchor': one[1]['trade_price'] = 100
    elif cause == 'quality':
        c['completed_structure_quality_checks'] = {
            'relative_leadership': False, 'higher_timeframe': False,
            'local_execution_book': False, 'market_breadth': False}
        fixture[1][1]['trade_price'] = 90
        for book in fixture[2]:
            for unit in book['orderbook_units']: unit['bid_size'] = 1
    elif cause == 'selling': alert['trade_flow_context'] = {'ready': True, 'as_of': now.timestamp(),
        'windows': [{'buy_krw': 0, 'sell_krw': 1e8}]}
    _, reasons = survive(c, fixture)
    assert any('구조·거래량' in r for r in reasons)


@pytest.mark.parametrize('cause', ['stale', 'missing', 'volume', 'gap', 'quality', 'quote', 'btc', 'risk', 'cost', 'wait'])
def test_dispatch_rejects_stale_or_invalid_structure_evidence(monkeypatch, cause):
    c = delivery_candidate(monkeypatch, structure_fixture(monkeypatch))
    assert not monitor.AlertDispatcher().candidate_delivery_reasons(c)
    if cause == 'stale': c['survival_structure_context']['as_of'] -= 11
    elif cause == 'missing': c.pop('survival_structure_context')
    elif cause == 'volume': c['survival_structure_context']['rolling_15m_volume_ratio'] = .99
    elif cause == 'gap': c['survival_structure_context']['rolling_history_contiguous'] = False
    elif cause == 'quality': c['survival_structure_quality_checks'] = {
        'relative_leadership': True, 'higher_timeframe': False,
        'local_execution_book': False, 'market_breadth': False}
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


def test_aged_raw_signal_can_use_new_completed_structure_and_calibrated_dispatch_floor(monkeypatch):
    fixture = structure_fixture(monkeypatch)
    fixture[0][5].update(relative_strength_percentile=3.0, relative_strength_eligible=False,
                          momentum_60m_pct=0.0, market_breadth_5m_pct=17.0)
    fixture[1][1]['trade_price'] = 90
    c = delivery_candidate(monkeypatch, fixture)
    assert not c['fresh_breakout_recheck']
    assert c['completed_structure_entry']
    assert c['setup_score_floor'] <= 80
    assert c['score'] >= c['setup_score_floor']
    c['score'] = 80
    c['condition_score'] = 80
    assert not monitor.AlertDispatcher().candidate_delivery_reasons(c)
