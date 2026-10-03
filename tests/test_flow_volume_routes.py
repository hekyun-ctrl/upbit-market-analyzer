"""New volume routes must survive verification and the final Telegram policy."""
from datetime import datetime, timedelta
import json
import asyncio
from pathlib import Path

import pytest
import candidate_analysis as ca
import monitor
from test_candidate_analysis import _config, _evaluate_flow, _flow_setup
from test_monitor import _flow_telegram_candidate


def rolling_fixture(monkeypatch):
    fixture = _flow_setup(monkeypatch)
    fixture[0][3][1]['candle_acc_trade_volume'] = 50
    return fixture


def survive(candidate, fixture):
    setup, hourly, books = fixture
    now, one, five, fifteen, _, alert, ticker = setup
    return ca.validate_candidate_survival(candidate, ticker, books, one, _config(),
        as_of=now, candles_5m=five, candles_15m=fifteen, candles_60m=hourly,
        trade_flow_context=alert.get('trade_flow_context'))


def test_completed_rolling_volume_connects_entry_survival_and_delivery(monkeypatch):
    fixture = rolling_fixture(monkeypatch)
    candidate, reasons = _evaluate_flow(fixture)
    assert candidate is not None, reasons
    assert candidate['flow_volume_mode'] == 'completed_rolling_breakout'
    assert candidate['completed_15m_volume_ratio'] < 1
    assert candidate['flow_entry_context']['rolling_15m_volume_ratio'] >= 1
    metrics, reasons = survive(candidate, fixture)
    assert not reasons
    candidate.update(metrics)
    now = fixture[0][0].timestamp()
    monkeypatch.setattr(monitor.time, 'time', lambda: now)
    template = _flow_telegram_candidate(now)
    for key in ('survival_confirmed', 'survival_seconds', 'survival_btc_5m_pct',
                'survival_btc_15m_pct', 'survival_btc_live_pct'):
        candidate[key] = template[key]
    candidate['dispatch_execution_quote'] = {
        **ca._quote_execution_context(fixture[2][-1],candidate['current_price'],_config()), 'as_of':now}
    assert not monitor.AlertDispatcher().candidate_delivery_reasons(candidate)
    assert '진행봉 제외' in monitor._candidate_text(candidate)


@pytest.mark.parametrize('cause', ['incomplete', 'gap', 'history', 'no_rolling_volume',
    'long_wick', 'stale_buying', 'selling', 'thin_nearby_bid', 'distant_only', 'btc'])
def test_rolling_route_cannot_admit_missing_or_adverse_evidence(monkeypatch,cause):
    fixture = rolling_fixture(monkeypatch)
    now, one, five, fifteen, btc, alert, _ = fixture[0]
    if cause == 'incomplete': five[1]['candle_date_time_utc'] = five[0]['candle_date_time_utc']
    elif cause == 'gap': five.pop(10)
    elif cause == 'history': five[:] = five[:63]  # 62 complete bars
    elif cause == 'no_rolling_volume':
        for b in five[2:]: b['candle_acc_trade_volume'] = 1000
    elif cause == 'long_wick': five[1]['high_price'] = 105
    elif cause == 'stale_buying': alert['trade_flow_context']['as_of'] -= 11
    elif cause == 'selling':
        for w in alert['trade_flow_context']['windows']: w['buy_krw'] = 0
    elif cause == 'thin_nearby_bid':
        for u in fixture[2][0]['orderbook_units']: u['bid_size'] = 1
    elif cause == 'distant_only':
        for u in fixture[2][0]['orderbook_units']: u['bid_price'] = 90
    elif cause == 'btc': btc[1]['trade_price'] = 98
    candidate, reasons = _evaluate_flow(fixture)
    assert candidate is None, (cause,candidate)
    assert reasons


def test_forming_candle_volume_does_not_change_completed_evidence(monkeypatch):
    fixture = rolling_fixture(monkeypatch)
    now, one, five, fifteen, *_ = fixture[0]
    before = ca._flow_entry_context(one,five,fifteen,now,_config())
    five[0].update(candle_acc_trade_volume=1e15, trade_price=9999)
    fifteen[0].update(candle_acc_trade_volume=1e15, trade_price=9999)
    assert ca._flow_entry_context(one,five,fifteen,now,_config()) == before


def test_distant_ask_wall_can_be_replaced_only_by_fresh_flow_and_nearby_depth(monkeypatch):
    fixture = _flow_setup(monkeypatch)
    for b in fixture[2]: b['total_ask_size'] = 4_000_000
    candidate, reasons = _evaluate_flow(fixture)
    assert candidate is not None, reasons
    assert candidate['orderbook_bid_ask_ratio'] < .2
    assert candidate['flow_execution_override']
    fixture[0][5].pop('trade_flow_context')
    candidate, reasons = _evaluate_flow(fixture)
    assert candidate is None


@pytest.mark.parametrize('cause',['stale_proof','stale_quote','missing_quote','thin_quote',
    'wide_quote','selling','btc','lost_level','chase','volume','mode','cost'])
def test_final_delivery_rechecks_new_route_evidence(monkeypatch,cause):
    now = 1800000100
    monkeypatch.setattr(monitor.time,'time',lambda:now)
    c = _flow_telegram_candidate(now)
    price = c['current_price']
    c.update(flow_volume_mode='completed_rolling_breakout',flow_execution_override=True,
        completed_15m_volume_ratio=.5,
        survival_flow_entry_context={'confirmed':True,'as_of':now,'volume_mode':'completed_rolling_breakout',
            'retest':False,'level':price*.999,'close':price,'volume_5m':2,'volume_15m':.5,
            'rolling_15m_volume_ratio':2},
        dispatch_execution_quote={'nearby_depth_supported':True,'spread_pct':.1,'as_of':now})
    assert not monitor.AlertDispatcher().candidate_delivery_reasons(c)
    if cause=='stale_proof': c['survival_flow_entry_context']['as_of']-=11
    elif cause=='stale_quote': c['dispatch_execution_quote']['as_of']-=11
    elif cause=='missing_quote': c.pop('dispatch_execution_quote')
    elif cause=='thin_quote': c['dispatch_execution_quote']['nearby_depth_supported']=False
    elif cause=='wide_quote': c['dispatch_execution_quote']['spread_pct']=.6
    elif cause=='selling':
        for w in c['survival_trade_flow']['windows']: w['buy_krw']=0
    elif cause=='btc': c['survival_btc_5m_pct']=-1
    elif cause=='lost_level': c['survival_flow_entry_context']['level']=price*1.001
    elif cause=='chase': c['survival_flow_entry_context']['close']=price*.99
    elif cause=='volume': c['survival_flow_entry_context']['rolling_15m_volume_ratio']=.99
    elif cause=='mode': c['survival_flow_entry_context']['volume_mode']='standard'
    elif cause=='cost': c['execution_cost_buffer_pct']=10
    assert monitor.AlertDispatcher().candidate_delivery_reasons(c)


def test_survival_cannot_reuse_initial_volume_proof(monkeypatch):
    fixture=rolling_fixture(monkeypatch)
    candidate,reasons=_evaluate_flow(fixture)
    assert candidate is not None,reasons
    for b in fixture[0][2][2:]: b['candle_acc_trade_volume']=1000
    _, reasons=survive(candidate,fixture)
    assert any('체결 매수' in r for r in reasons)


def public_fold_case(clock):
    fixture=json.loads((Path(__file__).parent/'fixtures/fold_completed_volume_20261003.json').read_text())
    case=next(c for c in fixture['cases'] if f'T{clock}:' in c['as_of'])
    keys=('candle_date_time_utc','opening_price','high_price','low_price','trade_price','candle_acc_trade_volume')
    return datetime.fromisoformat(case['as_of']),{int(u):[dict(zip(keys,row)) for row in rows]
        for u,rows in case['bars'].items()}


@pytest.mark.parametrize('clock,mode',[('06:45','contracting_first_retest'),
    ('07:40','completed_rolling_breakout'),('06:40',None),('06:50',None),('07:45',None)])
def test_public_fold_completed_candles_replay_without_future_evidence(clock,mode):
    now,bars=public_fold_case(clock)
    proof=ca._flow_entry_context(bars[1],bars[5],bars[15],now,_config())
    assert proof.get('volume_mode')==mode
    assert bool(proof.get('confirmed'))==bool(mode)
    if mode:
        assert not ca._explosive_bar_context(bars[1],bars[5],bars[15],now,impulse_min=1.5)['confirmed']
        assert proof['rolling_15m_volume_ratio']>=1
    if mode=='contracting_first_retest':
        assert proof['level']==95.8 and proof['volume_5m']==pytest.approx(.78796,rel=.001)
        assert proof['impulse_volume_ratio']>=4
    elif mode=='completed_rolling_breakout':
        assert proof['volume_5m']>=13 and proof['volume_15m']<.5


@pytest.mark.parametrize('cause',['support_break','long_wick','evaporated_volume','weak_impulse','gap'])
def test_contracting_pullback_rejects_distribution_and_missing_structure(cause):
    now,bars=public_fold_case('06:45')
    completed=ca.completed_context_candles(bars[5],5,now)
    if cause=='support_break': completed[0].update(low_price=93,trade_price=94)
    elif cause=='long_wick': completed[0]['high_price']=99
    elif cause=='evaporated_volume': completed[0]['candle_acc_trade_volume']=100
    elif cause=='weak_impulse': completed[2]['candle_acc_trade_volume']=100
    elif cause=='gap': bars[1].pop(5)
    assert not ca._flow_entry_context(bars[1],bars[5],bars[15],now,_config()).get('confirmed')


@pytest.mark.parametrize('cause',['stale','missing_retest','no_contraction','weak_impulse','no_volume','bad_mode'])
def test_contracting_pullback_final_policy_requires_fresh_explicit_proof(cause):
    now,bars=public_fold_case('06:45')
    proof=ca._flow_entry_context(bars[1],bars[5],bars[15],now,_config())
    candidate={'flow_volume_mode':'contracting_first_retest','current_price':96.4,
               'survival_flow_entry_context':proof}
    assert ca.flow_volume_verified(candidate,now.timestamp())
    if cause=='stale': proof['as_of']-=11
    elif cause=='missing_retest': proof['retest']=False
    elif cause=='no_contraction': proof['pullback_to_impulse_volume_ratio']=.76
    elif cause=='weak_impulse': proof['impulse_volume_ratio']=1.49
    elif cause=='no_volume': proof.update(volume_15m=.99,rolling_15m_volume_ratio=.99)
    elif cause=='bad_mode': proof['volume_mode']='standard'
    assert not ca.flow_volume_verified(candidate,now.timestamp())


def test_contracting_pullback_enters_and_survives_full_flow_lane(monkeypatch):
    # Public pattern, shifted into the independent execution/risk fixture.
    # This tests plumbing, not a claim that historical FOLD was actionable.
    fixture=_flow_setup(monkeypatch)
    now,one,five,fifteen,_,_,_=fixture[0]
    _, public=public_fold_case('06:45')
    bars=ca.completed_context_candles(public[5],5,datetime.fromisoformat('2026-10-03T06:45:10+00:00'))
    for i,bar in enumerate(bars):
        target=five[i+1]
        for key in ('opening_price','high_price','low_price','trade_price'):
            target[key]=bar[key]+4.6
        target['candle_acc_trade_volume']=bar['candle_acc_trade_volume']
    monkeypatch.setattr(ca,'_double_bollinger_context',_REAL_WB)
    # Resistance-risk behavior is tested separately with real levels; no
    # symbol-specific exclusion/target override exists in runtime code.
    monkeypatch.setattr(ca,'_resistances',lambda *a,**k:[])
    candidate,reasons=_evaluate_flow(fixture)
    assert candidate is not None,reasons
    assert candidate['flow_volume_mode']=='contracting_first_retest'
    assert .5<=candidate['completed_5m_volume_ratio']<1.5
    metrics,reasons=survive(candidate,fixture)
    assert not reasons
    candidate.update(metrics)
    epoch=now.timestamp()
    monkeypatch.setattr(monitor.time,'time',lambda:epoch)
    template=_flow_telegram_candidate(epoch)
    for key in ('survival_confirmed','survival_seconds','survival_btc_5m_pct',
                'survival_btc_15m_pct','survival_btc_live_pct'):
        candidate[key]=template[key]
    candidate['dispatch_execution_quote']={
        **ca._quote_execution_context(fixture[2][-1],candidate['current_price'],_config()),'as_of':epoch}
    assert not monitor.AlertDispatcher().candidate_delivery_reasons(candidate)
    monkeypatch.setenv('TELEGRAM_BOT_TOKEN','test-token')
    monkeypatch.setenv('TELEGRAM_CHAT_ID','test-chat')
    sent=[]
    class Client:
        def __init__(self,*a,**k): pass
        async def __aenter__(self): return self
        async def __aexit__(self,*a): pass
        async def post(self,url,json):
            sent.append(json['text'])
            class Response:
                def raise_for_status(self): pass
            return Response()
    monkeypatch.setattr(monitor.httpx,'AsyncClient',Client)
    assert asyncio.run(monitor.AlertDispatcher().send_candidate(candidate))
    assert len(sent)==1 and '돌파 후 거래량 감소' in sent[0]
    fixture[0][5].pop('trade_flow_context')
    rejected,reasons=_evaluate_flow(fixture)
    assert rejected is None


_REAL_WB=ca._double_bollinger_context
