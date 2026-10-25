"""Completed-minute acceleration/restart discovery. No transport or orders."""
from collections import defaultdict, deque
from dataclasses import dataclass
from datetime import datetime, timezone
from math import isfinite
from statistics import median
import hashlib
import json
import os
import time

VERSION = 'minute-surge-v1'
SIGNALS = {'minute_acceleration', 'minute_restart'}

def enabled():
    return os.getenv('UPBIT_MINUTE_SURGE_ENABLED', 'false').lower() in ('true','1','yes')

def epoch(bar):
    value = bar.get('candle_date_time_utc') or bar.get('time_utc')
    parsed=datetime.fromisoformat(str(value).replace('Z','+00:00'))
    return int((parsed.replace(tzinfo=timezone.utc) if parsed.tzinfo is None else parsed).timestamp())

def normalized(bar):
    result = dict(start=epoch(bar), open=float(bar['opening_price']), high=float(bar['high_price']),
        low=float(bar['low_price']), close=float(bar['trade_price']), value=float(bar['candle_acc_trade_price']))
    if not all(isfinite(result[k]) for k in ('open','high','low','close','value')):
        raise ValueError('non-finite minute')
    if not 0 < result['low'] <= min(result['open'],result['close']) <= max(result['open'],result['close']) <= result['high'] or result['value'] < 0:
        raise ValueError('invalid minute')
    return result

def proof(bars, market):
    """Use only completed observations; no RSI, compression or long-flow veto."""
    if len(bars) < 21:return None
    b=bars[-1];past=bars[-21:-1];p1,p2=past[-1],past[-2]
    if b['start']-past[0]['start'] > 35*60 or b['start']-p1['start'] != 60:return None
    baseline=median(x['value'] for x in past[-10:])
    ratio=b['value']/baseline if baseline>0 else None
    stairs=(p2['value']>0 and p1['value']>=1.8*p2['value'] and b['value']>=1.8*p1['value'])
    level=max(x['high'] for x in past[-10:])
    span=b['high']-b['low'];location=(b['close']-b['low'])/span if span else 0
    if not (b['value']>=20_000_000 and (ratio is not None and ratio>=3 or stairs)
            and b['close']>=level*1.0005 and b['close']>b['open'] and location>=.65):return None
    anchor=dict(bar=b,level=level,level_sources=[x for x in past[-10:] if x['high']==level],
        structural_low=min(b['low'],p1['low']),turnover_ratio=ratio,turnover_stairs=stairs,
        close_location=location,previous_value=p1['value'],baseline=baseline)
    anchor['setup_id']=hashlib.sha256(json.dumps([VERSION,market,b,level],sort_keys=True).encode()).hexdigest()
    return anchor

class MinuteSurgeEngine:
    def __init__(self):
        self.bars=defaultdict(lambda:deque(maxlen=120));self.live={};self.last_seq={};self.setups={}

    def warm(self, market, candles, now):
        existing={b['start']:b for b in self.bars[market]}
        for raw in candles:
            try:b=normalized(raw)
            except (ValueError,KeyError,TypeError):continue
            if b['start']+60<=now and b['start']>=now-7200:existing.setdefault(b['start'],b)
        self.bars[market]=deque(sorted(existing.values(),key=lambda b:b['start'])[-120:],maxlen=120)

    def reset_live(self):
        # Discard the first partial minute after a reconnect, retain completed evidence.
        self.live.clear();self.last_seq.clear()

    def completed(self, market, b, received_at):
        rows=self.bars[market]
        if rows and b['start']<=rows[-1]['start']:return None
        rows.append(dict(b));state=self.setups.get(market)
        if state and b['start']-state['start']>43200:
            self.setups.pop(market,None);state=None
        if state and b['start']>state['start'] and (b['close']<state['level'] or b['low']<=state['low']):
            state['failed']=True
        p=proof(list(rows),market)
        if not p:return None
        if state and b['start']-state['start']<120:return None
        restart=bool(state and state['failed'])
        parent=state.get('id') if restart else None
        self.setups[market]=dict(start=b['start'],level=p['level'],low=p['structural_low'],failed=False,id=p['setup_id'])
        return dict(signal='minute_restart' if restart else 'minute_acceleration',market=market,
            label='1분 재시동 후보' if restart else '1분 초기 가속 후보',price=b['close'],
            time_utc=datetime.fromtimestamp(received_at,timezone.utc).isoformat(),
            signal_id=p['setup_id'],setup_id=p['setup_id'],parent_signal_id=parent,
            surge_proof=p,source_bar_closed_at=b['start']+60,source_received_at=received_at,
            breakout_level=p['level'],change_1m_pct=(b['close']/b['open']-1)*100,
            trade_value_1m_krw=b['value'],volume_ratio_vs_previous_1m=b['value']/p['previous_value'] if p['previous_value'] else 0,
            internal_only=True,completed_bar_recheck=True,fast_leader=True)

    def update(self, market, price, volume, timestamp_ms, sequential_id=None, *, now=None):
        now=time.time() if now is None else now;second=int(timestamp_ms/1000);start=second-second%60
        if not all(isfinite(x) for x in (price,volume,now)) or price<=0 or volume<0 or not -1<=now-second<=10:return None
        state=self.setups.get(market)
        if state and (price<state['level'] or price<=state['low']):state['failed']=True
        if sequential_id is not None:
            if sequential_id<=self.last_seq.get(market,-1):return None
            self.last_seq[market]=sequential_id
        current=self.live.get(market);result=None
        if current and start<current['start']:return None
        if current and start>current['start']:
            if current['complete'] and start-current['start']==60 and 0<=now-start<=10:
                result=self.completed(market,{k:v for k,v in current.items() if k!='complete'},now)
            current=None
        if current is None:
            complete=market in self.live
            current=dict(start=start,open=price,high=price,low=price,close=price,value=0.,complete=complete)
            self.live[market]=current
        current['high']=max(current['high'],price);current['low']=min(current['low'],price)
        current['close']=price;current['value']+=price*volume
        return result

def verify_proof(alert, candles, now):
    p=alert.get('surge_proof') or {};source=p.get('bar') or {}
    if not p.get('setup_id') or not source:return None,'1분 가속 원문 근거 누락'
    if not 0<=now-source.get('start',now)-60<=90:return None,'1분 가속 근거 만료·미완료'
    rows=sorted((normalized(b) for b in candles if epoch(b)+60<=now),key=lambda b:b['start'])
    prefix=[b for b in rows if b['start']<=source['start']]
    check=proof(prefix,alert['market'])
    # Public candles can differ by harmless floating summation roundoff from
    # the same individual WS trades. Never tolerate a price/anchor revision.
    if (not check or check['bar']['start']!=source['start']
            or any(abs(check['bar'][k]-source[k])>max(1e-10,abs(source[k])*1e-9) for k in ('open','high','low','close'))
            or abs(check['bar']['value']-source['value'])>max(.01,source['value']*1e-6)
            or abs(check['level']-p['level'])>max(1e-10,p['level']*1e-9)
            or check['structural_low']!=p['structural_low']):return None,'1분 가속 공개 API 원문·돌파 기준 불일치'
    if any(b['low']<=p['structural_low'] or b['close']<p['level'] for b in rows if b['start']>source['start']):
        return None,'1분 가속 이후 구조 이탈'
    return p,None

def quote_guard(samples, ticker, config, now):
    from candidate_analysis import _quote_execution_context
    price=float(ticker.get('trade_price') or 0)
    quotes=[]
    if not 0<=now-float(ticker.get('timestamp',0))/1000<=10:return [],'1분 가속 현재가 시각 미확인'
    if len(samples)<3:return [],'1분 가속 반복 호가 부족'
    for sample in samples:
        if not 0<=now-float(sample.get('timestamp',0))/1000<=10:return [],'1분 가속 오래된 호가'
        q=_quote_execution_context(sample,price,config);quotes.append(q)
        if not q.get('nearby_depth_supported') or q.get('spread_pct',float('inf'))>config.hard_max_spread_pct:
            return quotes,'1분 가속 호가 깊이·최대 스프레드 미충족'
        if q['spread_pct']>config.max_spread_pct and not q.get('one_tick'):
            return quotes,'1분 가속 다중 호가 스프레드 초과'
    if len({s['timestamp'] for s in samples})<2:return quotes,'1분 가속 호가 반복 시각 미확인'
    return quotes,None

def evaluate(alert, ticker, samples, candles_1m, candles_5m, config, *, now, btc_ticker, btc_candles_5m):
    from candidate_analysis import _round_tick, _tick_size, _completed, validate_btc_survival
    from analysis import analyze_candles
    from exit_plan import exit_plan_metrics, execution_cost_metrics
    reject=[]
    p,reason=verify_proof(alert,candles_1m,now.timestamp())
    if reason:return None,[reason]
    quotes,reason=quote_guard(samples,ticker,config,now.timestamp())
    if reason:return None,[reason]
    price=float(ticker['trade_price']);tick=float(_tick_size(samples[-1],price))
    level=p['level'];close=p['bar']['close']
    if price<level or price>min(close*1.01,level*1.025):return None,['1분 가속 돌파 기준 이탈·추격 이격']
    if float(ticker.get('acc_trade_price_24h') or 0)<config.min_trade_value_24h_krw:return None,['1분 가속 24시간 유동성 부족']
    btc_metrics,btc_reject=validate_btc_survival(btc_ticker,btc_candles_5m,config,as_of=now)
    if btc_reject:return None,btc_reject
    stop=_round_tick(p['structural_low']-tick,tick,'down')
    if not 0<stop<price or (1-stop/price)*100>config.max_stop_loss_pct:return None,['1분 가속 구조 손절폭 초과']
    # A known overhead high is a ceiling. Without one the explicit 5/10%
    # projection is a plan assumption, never a structural-price forecast.
    historical=[b for b in _completed(candles_1m,1,now)+_completed(candles_5m,5,now)
                if epoch(b)<p['bar']['start']]
    barriers=[float(b['high_price']) for b in historical if float(b['high_price'])>price+tick]
    ceiling=min(barriers)-tick if barriers else None
    target1=_round_tick(min(price*1.05,ceiling) if ceiling else price*1.05,tick,'down')
    target2=_round_tick(min(price*1.10,ceiling) if ceiling else price*1.10,tick,'down')
    if target1<=price or target2<target1:return None,['1분 가속 목표 공간 부족']
    metrics=exit_plan_metrics(price,stop,target1,target2)
    spread=max(q['spread_pct'] for q in quotes)
    cost=execution_cost_metrics(price,stop,target1,target2,None,spread,max(.2,config.tick_spread_cost_buffer_pct))
    if metrics['risk_reward']<config.min_risk_reward or cost['net_risk_reward']<max(1.,config.min_risk_reward):return None,['1분 가속 비용 차감 손익비 부족']
    ratio=p['turnover_ratio'] or 0
    book_ratio=min(q['bid_value_krw']/q['ask_value_krw'] for q in quotes)
    score=20+(20 if ratio>=5 else 16)+(20 if p['close_location']>=.8 else 15)+(15 if book_ratio>=1.2 else 10)+(15 if cost['net_risk_reward']>=2.5 else 10)+10
    if score<config.min_score:return None,['1분 가속 조건점수 미달']
    c1=_completed(candles_1m,1,now);c5=_completed(candles_5m,5,now)
    candidate=dict(signal_id=alert['signal_id'],setup_id=p['setup_id'],parent_signal_id=alert.get('parent_signal_id'),
        time_utc=alert['time_utc'],market=alert['market'],signal='entry_candidate',label='조건부 진입 후보',
        source_signal=alert['signal'],selection_lane='minute_surge',surge_entry=True,surge_proof=p,
        is_reentry=False,condition_score=score,setup_score_floor=config.min_score,first_retest_confirmed=False,
        setup_anchor_price=level,setup_anchor_reason='completed_1m_breakout_level',
        setup_anchor_time_utc=datetime.fromtimestamp(p['bar']['start']+60,timezone.utc).isoformat(),
        setup_extension_pct=(price/level-1)*100,score=score,score_before_penalties=score,score_penalty=0,
        current_price=price,entry_reference_price=price,entry_low=max(level,_round_tick(price*.998,tick,'up')),
        entry_high=_round_tick(min(price*1.003,close*1.01,level*1.025),tick,'down'),
        chase_limit=_round_tick(min(close*1.01,level*1.025)+tick,tick,'up'),
        stop_price=stop,target_1=target1,target_2=target2,target_1_pct=(target1/price-1)*100,
        target_2_pct=(target2/price-1)*100,target_mode='1분 구조·비용 검증형',
        target_basis='known_historical_high_ceiling' if ceiling else 'explicit_5_10_percent_plan_projection',
        resistance_price=ceiling or target2,risk_reward_reference_price=target1,
        resistance_room_pct=((ceiling or target2)/price-1)*100,resistance_confirmed=bool(ceiling),
        breakout_level=level,holding_mode='initial_signal',stop_timeframe='완료 1분 구조',
        price_tick=tick,exit_plan=None,execution_cost=cost,execution_cost_buffer_pct=max(.2,config.tick_spread_cost_buffer_pct),
        minimum_net_risk_reward=max(1.,config.min_risk_reward),surge_execution_context=dict(as_of=now.timestamp(),quotes=quotes),
        spread_pct=spread,orderbook_bid_ask_ratio=book_ratio,valid_seconds=min(config.valid_seconds,90),
        suggested_position_pct=5,day_change_pct=float(ticker.get('signed_change_rate') or 0)*100,
        completed_1m_volume_ratio=ratio,volume_vs_previous=p['bar']['value']/max(p['previous_value'],1),
        close_position=p['close_location'],upper_wick_ratio=1-p['close_location'],
        rsi_1m=analyze_candles(c1).get('rsi14'),rsi_5m=analyze_candles(c5).get('rsi14') if len(c5)>=21 else None,
        reasons=['완료 1분 거래대금 가속·사전 고점 돌파','재시동 구조 별도 평가' if alert['signal']=='minute_restart' else '초기 가속 구조 평가',
                 '반복 호가·구조 손절·비용 차감 손익비 확인'],
        risk_notes=['새 경로의 수익성 미검증','목표는 계획값이며 도달 예측이 아님'],
        **metrics,**btc_metrics)
    if candidate['entry_low']>price or candidate['entry_high']<price:return None,['1분 가속 호가 단위 진입 구간 없음']
    return candidate,[]

def survival(candidate,ticker,samples,candles,config,now):
    alert=dict(market=candidate['market'],surge_proof=candidate['surge_proof'])
    p,reason=verify_proof(alert,candles,now.timestamp())
    if reason:return {},[reason]
    quotes,reason=quote_guard(samples,ticker,config,now.timestamp())
    if reason:return {},[reason]
    price=float(ticker['trade_price'])
    if price<candidate['breakout_level'] or price<=candidate['stop_price']:return {},['1분 가속 생존 중 구조 이탈']
    for b in candles:
        if epoch(b)>=p['bar']['start']+60 and epoch(b)+60<=now.timestamp() and float(b['low_price'])<=candidate['stop_price']:
            return {},['1분 가속 생존 중 최초 손절 침범']
    return dict(survival_surge_execution_context=dict(as_of=now.timestamp(),quotes=quotes),
        survival_orderbook_ratio=min(q['bid_value_krw']/q['ask_value_krw'] for q in quotes),
        survival_spread_pct=max(q['spread_pct'] for q in quotes)),[]

def dispatch_guard(candidate,quote,now,config):
    p=candidate.get('surge_proof') or {};b=p.get('bar') or {}
    context=candidate.get('survival_surge_execution_context') or {}
    if (candidate.get('surge_live_guard') or {}).get('invalidated'):return '1분 가속 최초 구조 침범 후 회복 불허'
    if not candidate.get('survival_confirmed') or float(candidate.get('survival_seconds') or 0)<8:return '1분 가속 생존 미확인'
    if not 0<=now-b.get('start',0)-60<=90:return '1분 가속 발송 근거 만료'
    if len(context.get('quotes',[]))<3 or not 0<=now-context.get('as_of',0)<=10:return '1분 가속 생존 호가 만료'
    if not quote or not 0<=now-quote.get('as_of',0)<=10:return '1분 가속 발송 직전 호가 누락'
    if any(not 0<=now-float(quote.get(k) or 0)<=10 for k in ('source_timestamp','ticker_timestamp')):return '1분 가속 발송 직전 원본 시각 만료'
    if not quote.get('nearby_depth_supported') or quote.get('spread_pct',float('inf'))>config.hard_max_spread_pct:return '1분 가속 발송 직전 호가 안전 미충족'
    if quote['spread_pct']>config.max_spread_pct and not quote.get('one_tick'):return '1분 가속 발송 직전 스프레드 초과'
    if quote.get('best_bid',0)<candidate['breakout_level']:return '1분 가속 발송 직전 돌파선 이탈'
    return None
