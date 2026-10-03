"""Official tick proof must never replace volume, fills, depth or net reward."""

from copy import deepcopy
import asyncio

import pytest

import candidate_analysis as ca
import monitor
from exit_plan import execution_cost_metrics
from test_candidate_analysis import _config, _evaluate_flow, _flow_setup
from test_monitor import _flow_telegram_candidate
from test_trade_flow import strong_flow


def book(bid=180, ask=181, bid_size=60000, ask_size=200000):
    return {"total_bid_size": bid_size, "total_ask_size": ask_size,
            "orderbook_units": [{"bid_price": bid, "ask_price": ask,
                                  "bid_size": bid_size, "ask_size": ask_size}]}


@pytest.mark.parametrize("price,tick", [(99.9,.1),(100,1),(999,1),(1000,1),
    (5000,5),(10000,10),(50000,50),(100000,100),(500000,500),(1000000,1000),
    (.1,.001),(.01,.0001),(.001,.00001),(.0001,.000001),(.00001,.0000001),
    (.000009,.00000001)])
def test_official_krw_tick_boundaries(price, tick):
    assert ca._krw_tick_size(price) == tick


def test_coarse_tick_includes_only_closest_executable_quote():
    quotes = [book(112,113)] * 3
    context = ca._tick_spread_context(quotes,112,_config())
    assert context['confirmed']
    assert context['spread_pct'] > _config().max_spread_pct
    assert all(q['ask_value_krw'] == 113 * 200000 for q in context['quotes'])
    boundary = ca._quote_execution_context(book(99.9,100),99.9,_config())
    assert boundary['one_tick'] and not boundary['coarse_tick']


def test_survival_tick_proof_allows_an_improving_percentage_spread():
    quotes = [book(200,201)] * 3
    assert not ca._tick_spread_context(quotes,200,_config())['confirmed']
    assert ca._tick_spread_context(quotes,200,_config(),require_coarse=False)['confirmed']


@pytest.mark.parametrize('price', [float('nan'),float('inf'),-1,0])
def test_bad_quote_prices_are_unverified(price):
    assert not ca._quote_execution_context(book(price,price+1),180,_config())['one_tick']


@pytest.mark.parametrize('cause', ['two_ticks','off_grid','crossed','missing','one_sample',
    'thin_bid','thin_ask','ratio','distant_depth','disabled','hard_cap','one_bad_sample'])
def test_tick_proof_rejects_unexecutable_or_unverified_books(cause):
    quotes = [book() for _ in range(3)]
    options = {}
    if cause == 'one_sample': quotes = quotes[:1]
    elif cause == 'disabled': options['tick_spread_enabled'] = False
    elif cause == 'hard_cap': options['hard_max_spread_pct'] = .51
    else:
        for quote in quotes:
            unit = quote['orderbook_units'][0]
            if cause == 'two_ticks': unit['ask_price'] = 182
            elif cause == 'off_grid': unit.update(bid_price=180.1,ask_price=181.1)
            elif cause == 'crossed': unit['ask_price'] = 180
            elif cause == 'missing': quote['orderbook_units'] = []
            elif cause == 'thin_bid': unit['bid_size'] = 1
            elif cause == 'thin_ask': unit['ask_size'] = 1
            elif cause == 'ratio': quote['total_bid_size'] = 10000
            elif cause == 'distant_depth':
                unit['bid_size'] = 1
                quote['orderbook_units'].append({'bid_price':170,'bid_size':1e9,
                                                'ask_price':190,'ask_size':1e9})
        if cause == 'one_bad_sample': quotes[0]['orderbook_units'][0]['ask_price'] = 182
    assert not ca._tick_spread_context(quotes,180,_config(**options))['confirmed']


def tick_fixture(monkeypatch):
    setup, hourly, _ = _flow_setup(monkeypatch)
    now, one, five, fifteen, _, alert, ticker = setup
    for bars in [one, five, fifteen, hourly]:
        for bar in bars:
            for key in ['opening_price','high_price','low_price','trade_price']:
                bar[key] += 79
    alert.update(price=180,breakout_level=179,trade_flow_context=strong_flow(now.timestamp(),180),
                 market_regime='neutral',market_breadth_5m_pct=45)
    ticker.update(trade_price=180,high_price=180.05)
    # Isolate execution qualification; existing WB-pattern tests remain unchanged.
    def evidence(one, five, fifteen, now, *, impulse_min=3):
        v5 = ca._volume_metrics(ca.completed_context_candles(five,5,now))[0]
        v15 = ca._volume_metrics(ca.completed_context_candles(fifteen,15,now))[0]
        return {'confirmed':impulse_min == 1.5 and v5 >= 1.5 and v15 >= 1,
                'low':179.5,'level':179,'close':180,'volume_5m':v5,
                'volume_previous_5m':2,'volume_15m':v15,'close_position':.9,'upper_wick':.1}
    monkeypatch.setattr(ca,'_explosive_bar_context',evidence)
    return setup, hourly, [book() for _ in range(3)]


def survive(candidate, fixture):
    setup, hourly, books = fixture
    now, one, five, fifteen, _, alert, ticker = setup
    return ca.validate_candidate_survival(candidate,ticker,books,one,_config(),as_of=now,
        candles_5m=five,candles_15m=fifteen,candles_60m=hourly,
        trade_flow_context=alert.get('trade_flow_context'))


def test_completed_flow_entry_and_survival_accept_a_cost_viable_official_tick(monkeypatch):
    fixture = tick_fixture(monkeypatch)
    candidate, reasons = _evaluate_flow(fixture)
    assert candidate is not None, reasons
    assert candidate['tick_spread_exception']
    assert candidate['stop_price'] == 178 and candidate['target_1'] == 185
    assert candidate['risk_reward'] == 2.5
    assert 1 < candidate['execution_cost']['net_risk_reward'] < 1.1
    result, reasons = survive(candidate,fixture)
    assert not reasons
    assert result['survival_execution_spread_context']['confirmed']
    assert result['survival_trade_flow']['ready']
    assert '추정 왕복 비용' in monitor._candidate_text(candidate)


@pytest.mark.parametrize('cause', ['missing_flow','stale_flow','selling','thin_book','two_ticks',
    'ratio','one_sample','five_volume','fifteen_volume','btc','chase','disabled','cost'])
def test_coarse_tick_entry_retains_other_filters(monkeypatch,cause):
    fixture = tick_fixture(monkeypatch)
    setup, _, books = fixture
    now, one, five, fifteen, btc, alert, ticker = setup
    options = {}
    if cause == 'missing_flow': alert.pop('trade_flow_context')
    elif cause == 'stale_flow': alert['trade_flow_context']['as_of'] -= 11
    elif cause == 'selling':
        for window in alert['trade_flow_context']['windows']: window['buy_krw'] = 0
    elif cause == 'thin_book':
        for b in books: b['orderbook_units'][0]['bid_size'] = 1
    elif cause == 'two_ticks':
        for b in books: b['orderbook_units'][0]['ask_price'] = 182
    elif cause == 'ratio':
        for b in books: b['total_bid_size'] = 10000
    elif cause == 'one_sample': books[:] = books[:1]
    elif cause == 'five_volume': five[1]['candle_acc_trade_volume'] = 100
    elif cause == 'fifteen_volume': fifteen[1]['candle_acc_trade_volume'] = 50
    elif cause == 'btc': btc[1]['trade_price'] = 98
    elif cause == 'chase': ticker['trade_price'] = 184
    elif cause == 'disabled': options['tick_spread_enabled'] = False
    elif cause == 'cost': options['tick_spread_cost_buffer_pct'] = .8
    candidate, reasons = _evaluate_flow(fixture,**options)
    assert candidate is None, (cause,candidate)
    assert reasons


@pytest.mark.parametrize('cause', ['missing_flow','stale_flow','selling','thin_book','two_ticks',
    'five_volume','fifteen_volume','cost','unmarked'])
def test_tick_survival_requires_current_qualifications(monkeypatch,cause):
    fixture = tick_fixture(monkeypatch)
    candidate, reasons = _evaluate_flow(fixture)
    assert candidate, reasons
    setup, _, books = fixture
    _, _, five, fifteen, _, alert, _ = setup
    if cause == 'missing_flow': alert.pop('trade_flow_context')
    elif cause == 'stale_flow': alert['trade_flow_context']['as_of'] -= 11
    elif cause == 'selling':
        for window in alert['trade_flow_context']['windows']: window['buy_krw'] = 0
    elif cause == 'thin_book':
        for b in books: b['orderbook_units'][0]['bid_size'] = 1
    elif cause == 'two_ticks':
        for b in books: b['orderbook_units'][0]['ask_price'] = 182
    elif cause == 'five_volume': five[1]['candle_acc_trade_volume'] = 100
    elif cause == 'fifteen_volume': fifteen[1]['candle_acc_trade_volume'] = 50
    elif cause == 'cost': candidate['target_1'] = 183
    elif cause == 'unmarked': candidate['tick_spread_exception'] = False
    _, reasons = survive(candidate,fixture)
    assert reasons, cause


def test_cost_model_reduces_reward_and_adds_to_risk_without_expansion_targets():
    costs = execution_cost_metrics(180,178,185,198,None,100/180.5,.2)
    assert costs['net_risk_reward'] == pytest.approx(1.08505231196)
    partial = {'mode':'partial_50_50','target_1_fraction':.5,'runner_fraction':.5,'runner_ceiling':185}
    metrics = execution_cost_metrics(180,178,183,185,partial,.55,.2)
    assert metrics['net_risk_reward'] == pytest.approx((4-1.35)/(2+1.35))
    for spread, buffer in [(-1,.2),(.5,-1),(float('nan'),.2),(.5,float('inf'))]:
        with pytest.raises(ValueError): execution_cost_metrics(180,178,185,198,None,spread,buffer)


def dispatch_candidate(now):
    candidate = _flow_telegram_candidate(now)
    proof = ca._tick_spread_context([book() for _ in range(3)],180,_config())
    proof['as_of'] = now
    quote = ca._quote_execution_context(book(),180,_config())
    quote['as_of'] = now
    candidate.update(score=88,current_price=180,entry_low=179,entry_high=181,chase_limit=182,
        stop_price=178,target_1=185,target_2=198,risk_reward_reference_price=185,resistance_price=185,
        tick_spread_exception=True,execution_spread_context=proof,
        survival_execution_spread_context=deepcopy(proof),dispatch_execution_quote=quote,
        survival_trade_flow=strong_flow(now,180),execution_cost_buffer_pct=.2,
        minimum_net_risk_reward=1,first_target_risk_reward=2.5)
    return candidate


def test_final_tick_policy_accepts_verified_cost_viable_flow(monkeypatch):
    now = 1800000100
    monkeypatch.setattr(monitor.time,'time',lambda:now)
    candidate = dispatch_candidate(now)
    refreshed, reason = monitor._revalidate_candidate_for_dispatch(candidate,180,min_risk_reward=2)
    assert refreshed and reason is None
    assert refreshed['execution_cost']['net_risk_reward'] > 1
    assert monitor.AlertDispatcher().candidate_delivery_reasons(refreshed) == []


def test_verified_tick_candidate_reaches_mock_delivery_with_cost_disclosure(monkeypatch):
    now = 1800000100
    monkeypatch.setattr(monitor.time,'time',lambda:now)
    monkeypatch.setenv('TELEGRAM_BOT_TOKEN','test-token')
    monkeypatch.setenv('TELEGRAM_CHAT_ID','test-chat')
    sent = []
    class Response:
        def raise_for_status(self): pass
    class Client:
        def __init__(self,*args,**kwargs): pass
        async def __aenter__(self): return self
        async def __aexit__(self,*args): pass
        async def post(self,url,json):
            sent.append(json['text'])
            return Response()
    monkeypatch.setattr(monitor.httpx,'AsyncClient',Client)
    refreshed, reason = monitor._revalidate_candidate_for_dispatch(dispatch_candidate(now),180,min_risk_reward=2)
    assert refreshed and not reason
    assert asyncio.run(monitor.AlertDispatcher().send_candidate(refreshed))
    assert len(sent) == 1 and '추정 왕복 비용' in sent[0]


@pytest.mark.parametrize('cause', ['missing_proof','stale_proof','missing_quote','stale_quote',
    'two_ticks','thin','selling','five_volume','fifteen_volume','survival','cost_tampering'])
def test_final_tick_policy_rejects_stale_or_deteriorated_evidence(monkeypatch,cause):
    now = 1800000100
    monkeypatch.setattr(monitor.time,'time',lambda:now)
    candidate = dispatch_candidate(now)
    if cause == 'missing_proof': candidate.pop('survival_execution_spread_context')
    elif cause == 'stale_proof': candidate['survival_execution_spread_context']['as_of'] -= 11
    elif cause == 'missing_quote': candidate.pop('dispatch_execution_quote')
    elif cause == 'stale_quote': candidate['dispatch_execution_quote']['as_of'] -= 11
    elif cause == 'two_ticks': candidate['dispatch_execution_quote']['one_tick'] = False
    elif cause == 'thin': candidate['dispatch_execution_quote']['depth_supported'] = False
    elif cause == 'selling':
        for window in candidate['survival_trade_flow']['windows']: window['buy_krw'] = 0
    elif cause == 'five_volume': candidate['completed_5m_volume_ratio'] = 1.49
    elif cause == 'fifteen_volume': candidate['completed_15m_volume_ratio'] = .99
    elif cause == 'survival': candidate['survival_seconds'] = 59
    elif cause == 'cost_tampering':
        candidate['execution_cost'] = {'net_risk_reward':100}
        candidate['dispatch_execution_quote']['spread_pct'] = 2
    assert monitor.AlertDispatcher().candidate_delivery_reasons(candidate), cause
