"""Counterfactual execution checks; later returns never enter admission math."""
import json
from datetime import datetime, timedelta
from pathlib import Path

import pytest
import candidate_analysis as ca
import monitor
from test_candidate_analysis import _config, _evaluate_flow
from test_completed_structure_entry import structure_fixture, delivery_candidate, survive


def replay(market):
    data = json.loads((Path(__file__).parent / 'fixtures/structure-execution-20261004.json').read_text())
    case = data['cases'][market]
    record = case['record']; metrics = record['screening_metrics']
    books = []
    for q in metrics['structure_book_context']['quotes']:
        # Logged local notionals are known. Full distant book is unavailable.
        books.append({'total_bid_size': metrics['orderbook_ratio'], 'total_ask_size': 1,
            'orderbook_units': [{'bid_price': q['best_bid'], 'ask_price': q['best_ask'],
                'bid_size': q['bid_value_krw'] / q['best_bid'],
                'ask_size': q['ask_value_krw'] / q['best_ask']}]})
    return case, books, datetime.fromisoformat(record['screening_snapshot']['as_of_utc']), datetime.fromisoformat(record['time_utc'])


@pytest.mark.parametrize('market,mode', [('KRW-SAND','executed_buy_share'), ('KRW-BAT','local_depth')])
def test_recorded_legal_tick_and_real_near_depth_are_not_failed_by_total_imbalance(market, mode):
    case, books, bar_clock, clock = replay(market)
    metrics = case['record']['screening_metrics']; current = case['record']['screening_snapshot']['current_price']
    # The old snapshot-start clock is before the flow updated during fetch.
    assert metrics['trade_flow_context']['as_of'] > bar_clock.timestamp()
    ctx = ca._structure_book_context(books, current, _config(), clock, metrics['trade_flow_context'])
    assert ctx['confirmed'] and ctx['coarse_tick'] and ctx['mode'] == mode
    assert all(q['one_tick'] and q['nearby_depth_supported'] for q in ctx['quotes'])
    proof = ca._completed_structure_context(case['bars']['minute1'],case['bars']['minute5'],case['bars']['minute15'],bar_clock,_config())
    assert proof['confirmed']
    assert metrics['structure_book_context']['confirmed'] is False


@pytest.mark.parametrize('cause', ['stale_flow','future_flow','sell_flow','too_few_trades','thin','far','multi_tick','too_wide','disabled_tick','one_snapshot'])
def test_recorded_weak_bid_cannot_bypass_execution_failures(cause):
    case, books, _, clock = replay('KRW-SAND'); flow = case['record']['screening_metrics']['trade_flow_context']
    cfg = _config()
    if cause == 'stale_flow': flow['as_of'] = clock.timestamp()-11
    elif cause == 'future_flow': flow['as_of'] = clock.timestamp()+1
    elif cause == 'sell_flow':
        for w in flow['windows']: w.update(buy_krw=0,sell_krw=1e8)
    elif cause == 'too_few_trades': flow['windows'][0]['count'] = 1
    elif cause == 'thin': books[0]['orderbook_units'][0]['bid_size'] = 1
    elif cause == 'far': books[0]['orderbook_units'][0]['bid_price'] = 90
    elif cause == 'multi_tick': books[0]['orderbook_units'][0]['ask_price'] = 106
    elif cause == 'too_wide': books[0]['orderbook_units'][0]['ask_price'] = 110
    elif cause == 'disabled_tick': cfg = _config(tick_spread_enabled=False)
    elif cause == 'one_snapshot': books = books[:1]
    assert not ca._structure_book_context(books,105,cfg,clock,flow)['confirmed']


def test_plateau_is_one_peak_but_two_separate_tests_preserve_real_resistance():
    def bars(highs): return [{'high_price':x} for x in reversed(highs)]
    flat = bars([101]*20)
    assert ca._swing_highs(flat) == []
    assert ca._resistances(100,{'high_price':101},flat,flat) == []
    plateau = bars([99,99,101,101,101,101,99,99])
    assert ca._swing_highs(plateau) == [101]
    assert ca._resistances(100,{'high_price':101},plateau,bars([99]*20)) == []
    repeated = bars([99,99,101,101,99,99,99,99,101,101,99,99])
    assert ca._swing_highs(repeated) == [101,101]
    assert ca._resistances(100,{'high_price':101},repeated,bars([99]*20)) == [101]
    assert ca._resistances(100,{'high_price':108},plateau,bars([99]*20)) == [108]


def test_qualified_five_breakout_ignores_only_obsolete_one_minute_gates(monkeypatch):
    fixture = structure_fixture(monkeypatch)
    now, one, five, _, _, alert, _ = fixture[0]
    alert['confirmation_started_at_utc'] = now.isoformat()
    # A forming candle, an old 1m shape/mean and a later signal do not alter
    # the already completed 5m event. The latest 1m must still hold its level.
    for b in one[2:22]: b['trade_price'] = 102
    one[1]['high_price'] = 103
    one[1]['candle_acc_trade_volume'] = 1
    c, reasons = _evaluate_flow(fixture, completed_structure_enabled=True)
    assert c is not None, reasons
    assert c['completed_structure_entry'] and not c['first_retest_confirmed']
    one[1]['trade_price'] = 99
    c, reasons = _evaluate_flow(fixture, completed_structure_enabled=True)
    assert c is None


def test_small_real_resistance_is_kept_and_sent_to_actual_net_risk_check(monkeypatch):
    fixture = structure_fixture(monkeypatch)
    fixture[0][5].update(market_regime='neutral',market_breadth_5m_pct=50)
    monkeypatch.setattr(ca,'_resistances',lambda *a,**k:[102.2])
    c, reasons = _evaluate_flow(fixture, completed_structure_enabled=True)
    metrics = fixture[0][5]['screening_metrics']
    assert metrics['resistance_admission_basis'] == 'actual_target_risk_and_cost'
    assert metrics['resistance_levels'] == [102.2]
    assert c is None
    assert not any('가까운 저항까지 여유 부족' in r for r in reasons)
    assert any('손익비' in r for r in reasons)


def test_real_close_resistance_with_sufficient_net_reward_can_pass(monkeypatch):
    fixture = structure_fixture(monkeypatch)
    fixture[0][5].update(market_regime='neutral',market_breadth_5m_pct=50)
    monkeypatch.setattr(ca,'_resistances',lambda *a,**k:[103.3])
    c,reasons = _evaluate_flow(fixture,completed_structure_enabled=True)
    assert c is not None,reasons
    assert c['target_1'] == c['resistance_price'] == 103.3
    assert c['first_target_risk_reward'] >= 2
    assert c['execution_cost']['net_risk_reward'] >= 1
    assert fixture[0][5]['screening_metrics']['resistance_room_pct'] < 2.5
    assert not c['leader_resistance_override']


def test_structure_cannot_inherit_a_fast_lane_reward_floor_or_raise_resistance(monkeypatch):
    fixture=structure_fixture(monkeypatch)
    alert=fixture[0][5]
    alert.update(signal='leader_volume_acceleration',fast_leader=True,early_trend=True,
        momentum_5m_pct=2,market_regime='neutral',market_breadth_5m_pct=50,
        preleader_volume_ratio_10m=10,preleader_volume_ratio_30m=10,
        leader_pullback_recheck=True)
    fixture[0][3][1]['candle_acc_trade_volume']=5000
    monkeypatch.setattr(ca,'_resistances',lambda *a,**k:[102.8])
    c,reasons=_evaluate_flow(fixture,completed_structure_enabled=True)
    assert c is None
    assert alert['screening_metrics']['completed_structure_entry']
    assert any('2.00' in r for r in reasons),reasons


def scaled_structure(monkeypatch):
    fixture = structure_fixture(monkeypatch)
    for bars in [fixture[0][1],fixture[0][2],fixture[0][3],fixture[1]]:
        for b in bars:
            for k in ('opening_price','high_price','low_price','trade_price'): b[k]*=6
    for k in ('price','breakout_level'): fixture[0][5][k]*=6
    for k in ('trade_price','high_price'): fixture[0][6][k]*=6
    for book in fixture[2]:
        book['orderbook_units']=[{'bid_price':605,'ask_price':606,'bid_size':12000,'ask_size':14000}]
    return fixture


def test_legal_tick_with_lower_local_ratio_survives_and_reaches_dispatch(monkeypatch):
    fixture=scaled_structure(monkeypatch)
    fixture[0][5].update(market_regime='neutral',market_breadth_5m_pct=50)
    # A stricter configured normal spread makes this one-tick book coarse.
    cfg=_config(completed_structure_enabled=True,max_spread_pct=.1)
    c,reasons=_evaluate_flow(fixture,completed_structure_enabled=True,max_spread_pct=.1)
    assert c is not None,reasons
    assert c['completed_structure_entry'] and c['structure_book_context']['coarse_tick']
    setup,hourly,books=fixture;now,one,five,fifteen,_,alert,ticker=setup
    update,reasons=ca.validate_candidate_survival(c,ticker,books,one,cfg,as_of=now,
        candles_5m=five,candles_15m=fifteen,candles_60m=hourly)
    assert not reasons
    c.update(update)
    monkeypatch.setattr(monitor.time,'time',lambda:now.timestamp())
    monkeypatch.setenv('CANDIDATE_COMPLETED_STRUCTURE_ENABLED','true')
    monkeypatch.setenv('CANDIDATE_MAX_SPREAD_PCT','.1')
    c.update(survival_confirmed=True,survival_seconds=60,
        survival_btc_5m_pct=0,survival_btc_15m_pct=0,survival_btc_live_pct=0)
    c['dispatch_execution_quote']={**ca._quote_execution_context(books[-1],606,cfg),'as_of':now.timestamp()}
    assert not monitor.AlertDispatcher().candidate_delivery_reasons(c)
    assert '공식 1호가 단위 비용 검증' in monitor._candidate_text(c)


def test_survival_rejects_deteriorating_net_cost_without_widening_stop(monkeypatch):
    fixture = structure_fixture(monkeypatch)
    c, reasons = _evaluate_flow(fixture,completed_structure_enabled=True)
    assert c is not None,reasons
    c['target_1'] = c['current_price'] + .1
    update,reasons = survive(c,fixture)
    assert any('비용 차감 손익비' in r for r in reasons)


def test_dispatch_does_not_infer_flow_from_an_old_book_flag(monkeypatch):
    c = delivery_candidate(monkeypatch,structure_fixture(monkeypatch))
    c['survival_structure_book_context']['flow_supported'] = True
    q = c['dispatch_execution_quote']; q['bid_value_krw'] = q['ask_value_krw']*.3
    c['survival_structure_trade_flow'] = None
    assert monitor.AlertDispatcher().candidate_delivery_reasons(c)


def test_clock_fix_keeps_forming_candle_out_of_structural_judgment(monkeypatch):
    fixture = structure_fixture(monkeypatch)
    now, one, five, fifteen, _, alert, _ = fixture[0]
    alert['trade_flow_context'] = {'source':'upbit_websocket_trade','ready':True,'as_of':now.timestamp()+5,
        'windows':[{'count':3,'buy_krw':1e7,'sell_krw':0,'close':101} for _ in range(3)]}
    assert not ca._structure_buy_flow(alert['trade_flow_context'],101,now.timestamp())
    clock = now+timedelta(seconds=6)
    assert ca._structure_buy_flow(alert['trade_flow_context'],101,clock.timestamp())
    original = ca._completed_structure_context(one,five,fifteen,now,_config())
    five[0].update(high_price=10000,trade_price=10000,candle_acc_trade_volume=1e15)
    assert ca._completed_structure_context(one,five,fifteen,now,_config()) == original


def test_updated_flow_uses_execution_clock_and_still_requires_real_source(monkeypatch):
    fixture = structure_fixture(monkeypatch)
    now,one,five,fifteen,btc,alert,ticker = fixture[0]
    alert.update(market_regime='neutral',market_breadth_5m_pct=50)
    alert['trade_flow_context']={'source':'upbit_websocket_trade','ready':True,'as_of':now.timestamp()+5,
        'windows':[{'count':3,'buy_krw':1e7,'sell_krw':0,'close':101} for _ in range(3)]}
    for book in fixture[2]:
        for u in book['orderbook_units']:u['bid_size'] = u['ask_size']*.3
    def check(clock):
        return ca.evaluate_candidate(alert,ticker,fixture[2][-1],one,five,
            _config(double_bb_enabled=True,completed_structure_enabled=True),candles_15m=fifteen,
            candles_60m=fixture[1],btc_candles_5m=btc,btc_candles_15m=btc,
            orderbook_samples=fixture[2],as_of=now,execution_as_of=clock)
    c,_=check(now)
    assert c is None
    c,reasons=check(now+timedelta(seconds=6))
    assert c is not None,reasons
    assert c['structure_book_context']['flow_supported']
    alert['trade_flow_context']['source']='unknown'
    c,_=check(now+timedelta(seconds=6))
    assert c is None


def test_bat_replay_still_fails_actual_cost_not_old_minute_volume(monkeypatch):
    case, books, clock, execution_clock = replay('KRW-BAT');r=case['record'];m=r['screening_metrics'];bars=case['bars']
    alert={**r,'signal':r['source_signal'],'price':147,'breakout_level':147,
        'time_utc':'2026-10-04T12:05:25+00:00','relative_strength_ready':True,
        'relative_strength_eligible':True,'market_breadth_5m_pct':30,'trade_flow_context':m['trade_flow_context']}
    ticker={'trade_price':148,'signed_change_rate':.06,'acc_trade_price_24h':m['trade_value_24h_krw'],
        'high_price':max(b['high_price'] for b in bars['minute5'])}
    c,reasons=ca.evaluate_candidate(alert,ticker,books[-1],bars['minute1'],bars['minute5'],
        _config(completed_structure_enabled=True,double_bb_enabled=True),candles_15m=bars['minute15'],
        candles_60m=bars['minute60'],btc_candles_5m=bars['btc5'],btc_candles_15m=bars['btc15'],
        btc_ticker={'trade_price':bars['btc5'][0]['trade_price'],'signed_change_rate':0},
        orderbook_samples=books,as_of=clock,execution_as_of=execution_clock)
    assert c is None
    assert alert['screening_metrics']['completed_structure_entry']
    assert reasons and all('손익비' in r for r in reasons)
    assert alert['screening_metrics']['execution_cost']['net_risk_reward'] < 1
    # Future returns are audit labels only; deleting them cannot change entry.
    assert case['outcome_from_initial_capture']['return_pct'] == -2.04
