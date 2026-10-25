import copy
from datetime import datetime, timezone
from dataclasses import replace
import pytest
from minute_surge import MinuteSurgeEngine, proof, normalized, verify_proof, evaluate, survival, dispatch_guard, quote_guard
from candidate_analysis import CandidateConfig, evaluate_candidate, validate_candidate_survival

NOW=1792932002
START=NOW-NOW%60-60

def bar(start,open=10000.,high=10010.,low=9990.,close=10000.,value=10_000_000.):
    return dict(candle_date_time_utc=datetime.fromtimestamp(start,timezone.utc).replace(tzinfo=None).isoformat(),
        opening_price=open,high_price=high,low_price=low,trade_price=close,candle_acc_trade_price=value,candle_acc_trade_volume=value/close)

def fixture():
    rows=[bar(START-60*i) for i in range(20,0,-1)]
    rows.append(bar(START,10010.,10110.,10000.,10100.,100_000_000.))
    p=proof([normalized(b) for b in rows],'KRW-TEST')
    alert=dict(market='KRW-TEST',signal='minute_acceleration',signal_id=p['setup_id'],surge_proof=p,
        time_utc=datetime.fromtimestamp(NOW,timezone.utc).isoformat())
    ticker=dict(trade_price=10100.,timestamp=NOW*1000,signed_change_rate=2.5,acc_trade_price_24h=100_000_000_000.)
    quotes=[dict(timestamp=(NOW-i)*1000,orderbook_units=[dict(bid_price=10100.,ask_price=10110.,bid_size=1000.,ask_size=1000.)]) for i in (4,2,0)]
    five=[bar(NOW//300*300-300*i) for i in range(21,0,-1)]
    return alert,ticker,quotes,rows,five

def plan():
    a,t,q,one,five=fixture();cfg=CandidateConfig.from_env()
    c,reasons=evaluate(a,t,q,one,five,cfg,now=datetime.fromtimestamp(NOW,timezone.utc),btc_ticker=t,btc_candles_5m=five)
    assert c is not None,reasons
    return c,cfg

def test_prior_anchor_excludes_trigger_high_and_future_candles():
    a,_,_,rows,_=fixture();assert a['surge_proof']['level']==10010
    assert all(b['start']<START for b in a['surge_proof']['level_sources'])
    assert verify_proof(a,rows+[bar(START+60,high=99999.)],NOW)[1] is None
    assert verify_proof(a,rows,START+59)[1]

def test_amount_and_price_breakout_are_both_required():
    _,_,_,rows,_=fixture();n=[normalized(b) for b in rows]
    n[-1]['value']=1_000_000;assert proof(n,'KRW-TEST') is None
    n[-1]['value']=1e8;n[-1]['close']=10000;assert proof(n,'KRW-TEST') is None

def test_stairs_do_not_require_long_accumulation_or_compression():
    _,_,_,rows,_=fixture();n=[normalized(b) for b in rows]
    for b in n[:-3]:b['value']=50e6;b['high']=10050
    n[-3]['value']=4.41e6;n[-2]['value']=21.62e6;n[-1]['value']=58.8e6
    assert proof(n,'KRW-TEST')['turnover_stairs']

def test_same_anchor_deduplicates_and_failed_setup_can_restart():
    a,_,_,rows,_=fixture();e=MinuteSurgeEngine();e.warm('KRW-TEST',rows[:-1],NOW)
    first=e.completed('KRW-TEST',normalized(rows[-1]),NOW)
    assert first['signal']=='minute_acceleration'
    assert e.completed('KRW-TEST',normalized(rows[-1]),NOW) is None
    e.completed('KRW-TEST',normalized(bar(START+60,10000,10010,9980,9990)),NOW+60)
    later=normalized(bar(START+120,10100,10210,10090,10200,200e6))
    second=e.completed('KRW-TEST',later,NOW+120)
    assert second['signal']=='minute_restart' and second['parent_signal_id']==first['signal_id']
    assert second['setup_id']!=first['setup_id']

def test_warmup_and_reconnect_never_replay_a_historical_alert():
    _,_,_,rows,_=fixture();e=MinuteSurgeEngine();assert e.warm('KRW-TEST',rows,NOW) is None
    assert not e.setups
    e.update('KRW-TEST',10100,1000,(START+60)*1000,1,now=START+60)
    assert e.update('KRW-TEST',10110,1000,(START+120)*1000,2,now=START+120) is None
    e.reset_live();assert not e.live and e.bars['KRW-TEST']

def test_late_duplicate_and_out_of_order_ticks_do_not_inflate_turnover():
    e=MinuteSurgeEngine();at=NOW-NOW%60
    e.update('KRW-TEST',10000,1,at*1000,10,now=at)
    e.update('KRW-TEST',10000,100,at*1000,10,now=at)
    e.update('KRW-TEST',10000,100,(at-60)*1000,9,now=at)
    assert e.live['KRW-TEST']['value']==10000

def test_public_api_price_revisions_reject_but_roundoff_is_tolerated():
    a,_,_,rows,_=fixture();rows[-1]['candle_acc_trade_price']+=.001
    assert verify_proof(a,rows,NOW)[1] is None
    rows[-1]['trade_price']+=10
    assert verify_proof(a,rows,NOW)[1]

def test_rsi_above_80_and_large_day_gain_do_not_veto_verified_plan():
    c,_=plan();assert c['rsi_1m']>80 and c['day_change_pct']>100
    assert c['stop_price']<c['surge_proof']['structural_low']
    assert c['execution_cost']['net_risk_reward']>=2

def test_structure_is_not_tightened_to_force_risk_admission():
    a,t,q,one,five=fixture();one[-2]['low_price']=9700
    p=proof([normalized(b) for b in one],'KRW-TEST');a['surge_proof']=p
    c,reasons=evaluate(a,t,q,one,five,CandidateConfig.from_env(),now=datetime.fromtimestamp(NOW,timezone.utc),btc_ticker=t,btc_candles_5m=five)
    assert c is None and '손절폭' in reasons[0]

def test_old_origin_does_not_replace_new_structure_but_chase_still_rejects():
    a,t,q,one,five=fixture();a['origin_signal_price']=1000
    c,_=evaluate(a,t,q,one,five,CandidateConfig.from_env(),now=datetime.fromtimestamp(NOW,timezone.utc),btc_ticker=t,btc_candles_5m=five)
    assert c
    t['trade_price']=11000
    for sample in q:
        sample['orderbook_units'][0].update(bid_price=11000.,ask_price=11010.)
    c,reasons=evaluate(a,t,q,one,five,CandidateConfig.from_env(),now=datetime.fromtimestamp(NOW,timezone.utc),btc_ticker=t,btc_candles_5m=five)
    assert c is None and '추격' in reasons[0]

def test_stale_insufficient_or_copied_book_cannot_authorize_entry():
    _,t,q,_,_=fixture();cfg=CandidateConfig.from_env()
    assert quote_guard(q[:2],t,cfg,NOW)[1]
    assert quote_guard([q[0]]*3,t,cfg,NOW)[1]
    q[-1]['timestamp']=(NOW-20)*1000;assert quote_guard(q,t,cfg,NOW)[1]

def test_intermediate_stop_breach_is_not_erased_by_recovery():
    c,cfg=plan();_,t,q,one,_=fixture()
    one.append(bar(START+60,10100,10200,9900,10100,100e6))
    _,reasons=survival(c,t,q,one,cfg,datetime.fromtimestamp(START+121,timezone.utc))
    assert reasons

def test_dispatch_requires_fresh_survival_quote_and_expiry():
    c,cfg=plan();_,t,q,one,_=fixture();when=datetime.fromtimestamp(NOW,timezone.utc)
    metrics,reasons=survival(c,t,q,one,cfg,when);assert not reasons
    c.update(metrics,survival_confirmed=True,survival_seconds=8)
    quote=dict(metrics['survival_surge_execution_context']['quotes'][-1],as_of=NOW,source_timestamp=NOW,ticker_timestamp=NOW)
    assert dispatch_guard(c,quote,NOW,cfg) is None
    assert dispatch_guard(c,{},NOW,cfg)
    assert dispatch_guard(c,quote,NOW+100,cfg)

def test_feature_flag_off_and_forming_bar_never_promote(monkeypatch):
    a,t,q,one,five=fixture();cfg=CandidateConfig.from_env();when=datetime.fromtimestamp(NOW,timezone.utc)
    monkeypatch.delenv('UPBIT_MINUTE_SURGE_ENABLED',raising=False)
    assert evaluate_candidate(a,t,q[-1],one,five,cfg,orderbook_samples=q,as_of=when,execution_as_of=when)[0] is None
    monkeypatch.setenv('UPBIT_MINUTE_SURGE_ENABLED','true')
    c,reasons=evaluate_candidate(a,t,q[-1],one+[bar(START+60,high=99999)],five,cfg,
        orderbook_samples=q,as_of=when,execution_as_of=when,btc_ticker=t,btc_candles_5m=five)
    assert c,reasons

def test_candidate_text_and_final_cost_recheck_share_the_actual_plan():
    from monitor import _candidate_text,_revalidate_candidate_for_dispatch
    c,cfg=plan();text=_candidate_text(c)
    assert '1분 초기 가속형' in text and '실제 매수 체결·' not in text
    refreshed,reason=_revalidate_candidate_for_dispatch(c,c['current_price'],min_risk_reward=cfg.min_risk_reward,max_stop_loss_pct=cfg.max_stop_loss_pct)
    assert refreshed and not reason


def test_recovered_live_breach_remains_rejected_at_dispatch(monkeypatch):
    import monitor
    c,cfg=plan();c['surge_live_guard']={'invalidated':False}
    a=monitor.CandidateAnalyzer(cfg,monitor.AlertDispatcher())
    a._surge_pending['KRW-TEST']=c
    a.observe_price('KRW-TEST',c['stop_price']-10)
    a.observe_price('KRW-TEST',c['current_price'])
    assert c['surge_live_guard']['invalidated']
    assert '침범' in dispatch_guard(c,{},NOW,cfg)


def test_full_candidate_pipeline_uses_survival_and_final_quote_without_observation_message(monkeypatch):
    import asyncio
    from unittest.mock import AsyncMock
    import monitor
    clock=[float(NOW)]
    class Frozen(datetime):
        @classmethod
        def now(cls,tz=None):return datetime.fromtimestamp(clock[0],tz or timezone.utc)
    monkeypatch.setenv('UPBIT_MINUTE_SURGE_ENABLED','true')
    monkeypatch.setattr(monitor,'datetime',Frozen)
    monkeypatch.setattr(monitor.time,'time',lambda:clock[0])
    monkeypatch.setattr(monitor,'MONITOR_STATE',monitor.MonitorState())
    async def sleep(seconds):clock[0]+=seconds
    monkeypatch.setattr(monitor.asyncio,'sleep',sleep)
    a,t,q,one,five=fixture();a.update(price=10100,completed_bar_recheck=True,fast_leader=True,setup_id=a['signal_id'])
    async def snapshot(market):
        _,ticker,books,minute,f=fixture()
        ticker['timestamp']=clock[0]*1000
        for i,b in enumerate(books):b['timestamp']=(clock[0]-4+i*2)*1000
        now=Frozen.now(timezone.utc)
        return dict(ticker=ticker,orderbooks=books,candles_1m=minute,candles_5m=f,candles_15m=f,
            btc_ticker=ticker,btc_candles_5m=f,btc_candles_15m=f,as_of=now,requested_at=now,fetched_at=now)
    class Client:
        async def ticker(self,m):return (await snapshot(m))['ticker']
        async def orderbook(self,m):return (await snapshot(m))['orderbooks'][-1]
        async def close(self):pass
    monkeypatch.setattr(monitor,'UpbitPublicClient',Client)
    dispatcher=monitor.AlertDispatcher()
    analyzer=monitor.CandidateAnalyzer(replace(CandidateConfig.from_env(),enabled=True),dispatcher)
    monkeypatch.setattr(analyzer,'_market_snapshot',snapshot)
    delivered=[]
    async def send(candidate):
        assert not dispatcher.candidate_delivery_reasons(candidate)
        assert candidate['survival_seconds']==8
        assert candidate['execution_cost']['net_risk_reward']>=2
        delivered.append(candidate);return True,False
    monkeypatch.setattr(analyzer,'_send_candidate_once',send)
    asyncio.run(analyzer._analyze(a))
    assert len(delivered)==1
    assert 'KRW-TEST' in analyzer._lifecycles
    assert not analyzer._surge_pending
