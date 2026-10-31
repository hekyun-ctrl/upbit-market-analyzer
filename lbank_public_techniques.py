"""Public-technique hypotheses, not replicas or validated trading signals.

Nassi's discretionary tick grouping is operationalized explicitly; Kim's
20/2 close + 4/4 open bands retain a separate 1h study and a labelled 5m
adaptation. Box retests use only levels known before the breakout. Nothing
here vetoes, ranks, changes risk, or sends an existing live entry alert.
"""
import copy
import json
import math
import statistics
from datetime import datetime, timedelta, timezone

from lbank_native_entry import SOURCE
from lbank_performance import PerformanceBook
from lbank_trend_runner import atr_wilder
import lbank_performance_audit as audit

KST=timezone(timedelta(hours=9))

VERSION = "public_techniques_v1"
LABELS = {"grouped_decline_5m":"5분 3단하락 반등 실험",
          "double_bb_5m":"5분 더블밴드 변형 실험",
          "double_bb_1h":"1시간 더블밴드 실험",
          "box_retest_5m":"5분 박스 돌파·재지지 실험"}


def valid(bars, period):
    if not bars:
        return False
    for i,b in enumerate(bars):
        values = [float(b[k]) for k in ("open","high","low","close","volume")]
        o,h,l,c,v = values
        if (not all(math.isfinite(x) for x in values) or min(o,h,l,c)<=0 or v<0
                or h<max(o,c,l) or l>min(o,c,h) or b.get("quality")!="stream_closed"
                or b["start"]%period or (i and b["start"]-bars[i-1]["start"]!=period)):
            return False
    return True


def band(values, deviation):
    mean=statistics.mean(values); std=statistics.pstdev(values)
    return mean-deviation*std, mean+deviation*std


def aggregate_hourly(bars):
    """Never fill missing constituent bars or use a partial hour."""
    by_start={b['start']:b for b in bars}; result=[]
    for start in sorted(t for t in by_start if t%3600==0):
        group=[by_start.get(start+i*300) for i in range(12)]
        if any(b is None for b in group) or not valid(group,300):
            continue
        result.append(dict(start=start,open=group[0]['open'],close=group[-1]['close'],
                           high=max(b['high'] for b in group),low=min(b['low'] for b in group),
                           volume=sum(b['volume'] for b in group),quality='stream_closed'))
    return result


def _event(strategy,bars,period,d,anchor,level,atr,barrier=None,**details):
    return dict(id=f"{VERSION}:{strategy}:{d}:{bars[-1]['start']}",strategy=strategy,
                direction=d,available_at=bars[-1]['start']+period,
                expires_at=bars[-1]['start']+period+900,
                structure_anchor=anchor,level=level,zone=atr*.2,barrier=barrier,
                paper_only=True,details=details)


def _patterns(bars, period):
    if len(bars)<22:
        return []
    atr=atr_wilder(bars[-22:])
    if atr is None or atr<=0:
        return []
    last=bars[-1]; result=[]; closes=[b['close'] for b in bars]
    lo20,hi20=band(closes[-20:],2)
    lo4,hi4=band([b['open'] for b in bars[-4:]],4)
    slope=(closes[-1]-closes[-21])/20  # exact consecutive SMA20 difference
    for d in (1,-1):
        touch=last['low']<=min(lo20,lo4) if d==1 else last['high']>=max(hi20,hi4)
        if touch and d*slope>0:
            result.append(_event('double_bb_1h' if period==3600 else 'double_bb_5m',
                bars,period,d,last['low'] if d==1 else last['high'],last['close'],atr,
                max(b['high'] for b in bars[-20:-1]) if d==1 else min(b['low'] for b in bars[-20:-1]),
                lower20=lo20,upper20=hi20,lower4=lo4,upper4=hi4,sma20_slope=slope,
                price_sources='close20_open4',timeframe_seconds=period))
    if period!=300:
        return result
    # Our reproducible grouping, NOT the creator's complete discretionary rule.
    # Equal/nearby closes merge; material recovery resets. A step is half the
    # prior 20-bar median true range (bounded below by 1bp of price).
    prior=bars[-22:-1]
    ranges=[max(b['high']-b['low'],abs(b['high']-a['close']),abs(b['low']-a['close']))
            for a,b in zip(prior,prior[1:])]
    step=max(statistics.median(ranges)*.5,last['close']*.0001)
    recent=bars[-10:]; level=recent[0]['close']; count=0; stepped=False; anchor=recent[0]['low']
    for b in recent[1:]:
        stepped=False
        if b['close']>=level+step:
            level=b['close'];count=0;anchor=b['low']
        elif b['close']<=level-step and b['close']<b['open']:
            count+=1;level=b['close'];stepped=True
        anchor=min(anchor,b['low'])
    if count==3 and stepped:
        result.append(_event('grouped_decline_5m',bars,300,1,anchor,last['close'],atr,
                      max(b['high'] for b in bars[-10:-1]),step=step,grouped_steps=3,
                      adaptation='completed_close_grouping_no_averaging_down'))
    # Frozen box precedes BOTH the breakout and retest candles.
    box=bars[-22:-2]; br=bars[-2]; hi=max(b['high'] for b in box);lo=min(b['low'] for b in box)
    zone=.2*atr
    for d in (1,-1):
        level=hi if d==1 else lo
        broken=d*(br['close']-level)>zone and d*(br['open']-level)<=zone
        touched=(last['low']<=level+zone and last['low']>=level-zone if d==1
                 else last['high']>=level-zone and last['high']<=level+zone)
        if broken and touched and d*(last['close']-level)>0 and d*slope>0:
            anchor=min(last['low'],level-zone) if d==1 else max(last['high'],level+zone)
            result.append(_event('box_retest_5m',bars,300,d,anchor,level,atr,
                                box_high=hi,box_low=lo,box_end=box[-1]['start']))
    return result


def analyze_bars(bars, now):
    """A causal setup audit can use this without inventing minute candles."""
    empty=dict(version=VERSION,ready=False,setups=[],live_gate=False)
    try:
        if (not math.isfinite(now) or len(bars)<22 or not valid(bars,300)
                or bars[-1]['start']!=int(now//300)*300-300):
            return dict(empty,reason='native_data_unavailable')
        events={}
        for n in range(max(22,len(bars)-2),len(bars)+1):
            for e in _patterns(bars[:n],300):events[e['id']]=e
        hours=aggregate_hourly(bars)
        hourly_ready=(len(hours)>=22 and valid(hours,3600)
                      and hours[-1]['start']==int(now//3600)*3600-3600)
        if hourly_ready:
            for e in _patterns(hours,3600):events[e['id']]=e
        setups=[]
        for e in events.values():
            following=[b for b in bars if b['start']>=e['available_at']]
            key='low' if e['direction']==1 else 'high'
            invalid=any(e['direction']*(b[key]-e['structure_anchor'])<0 for b in following)
            if e['available_at']<=now<=e['expires_at'] and not invalid:setups.append(e)
        last=bars[-1]; width=last['high']-last['low']; med=statistics.median(b['volume'] for b in bars[-21:-1])
        return dict(empty,ready=True,bar_start=last['start'],setups=sorted(setups,key=lambda e:e['id']),
                    hourly_ready=hourly_ready,relative_volume=last['volume']/med if med>0 else None,
                    close_location=(last['close']-last['low'])/width if width else .5,
                    note='generic_candle_volume_features_not_an_AOA_entry_rule')
    except (KeyError,TypeError,ValueError,OverflowError,IndexError):
        return dict(empty,reason='invalid_native_data')


def analyze(state,now):
    if not state or state.get('source')!=SOURCE:
        return dict(version=VERSION,ready=False,setups=[],live_gate=False,reason='native_data_unavailable')
    return analyze_bars(state.get('research_five_bars',state.get('five_bars',[])),now)


def confirmation(state,event,now):
    try:
        bars=state['minute_bars'][-4:];last=bars[-1];d=event['direction']
        if (len(bars)<4 or not valid(bars,60) or last['start']!=int(now//60)*60-60
                or not event['available_at']<=last['start']<event['expires_at']):return False
        key='low' if d==1 else 'high'
        if any(d*(b[key]-event['structure_anchor'])<0 for b in bars if b['start']>=event['available_at']):return False
        level=max(b['high'] for b in bars[:-1]) if d==1 else min(b['low'] for b in bars[:-1])
        return d*(last['close']-last['open'])>0 and d*(last['close']-level)>0 and d*(last['close']-event['level'])>0
    except (KeyError,ValueError,TypeError,IndexError):
        return False


def annotate(plan,state,now,reference_balance):
    """Only after all live gates. Presentation/frozen evidence, never a veto."""
    try:
        ctx=analyze(state,now)
        labels=[LABELS[e['strategy']] for e in ctx['setups'] if e['direction']==plan['direction']]
        ctx['label']=' / '.join(dict.fromkeys(labels))
        if not math.isfinite(reference_balance) or reference_balance<=0:return plan
        ctx['reference_exposure_multiple']=plan['notional']/reference_balance
        ctx['reference_stop_loss_pct']=plan['net_loss']/reference_balance*100
        ctx['reference_balance']=reference_balance
        return dict(plan,public_techniques=copy.deepcopy(ctx))
    except Exception:
        return plan


class TechniqueStudy:
    """Independent, restart-safe prospective outcomes. No live alert state."""
    def __init__(self,path,balance,*,fee=.0006,slippage=.0002,funding=.0001):
        self.book=PerformanceBook(path,balance)
        self.costs=dict(fee=fee,slippage=slippage,funding=funding)
        self.book.db.execute('CREATE TABLE IF NOT EXISTS technique_candidates(id TEXT PRIMARY KEY,first_at REAL,price REAL,status TEXT,detail TEXT)')

    def step(self,now,price,state,calculate_plan,context_gate,*,baseline_reason):
        outcomes=self.book.observe(now,price,native_state=state)
        ctx=analyze(state,now); decisions=[]
        for e in ctx['setups']:
            reason='qualified'; d=e['direction']
            if not confirmation(state,e,now):reason='await_closed_minute_reaction'
            elif abs(price/state['prices'][-1]-1)>.001:reason='price_deviation'
            elif not context_gate(state,d,'range_rebound' if e['strategy']=='grouped_decline_5m' else 'trend_retest')[0]:reason='context'
            elif len(state['rsi5']['rsi'])<2 or d*(state['rsi5']['rsi'][-1]-state['rsi5']['rsi'][-2])<=0:reason='rsi_direction'
            plan=calculate_plan(state['prices'],d,e,native_state=state) if reason=='qualified' else None
            if reason=='qualified' and plan is None:reason='risk_or_target'
            if plan is not None:
                row=self.book.db.execute('SELECT * FROM technique_candidates WHERE id=?',(e['id'],)).fetchone()
                if row is None:
                    detail=dict(event=e,baseline_reason=baseline_reason)
                    with self.book.db:self.book.db.execute('INSERT INTO technique_candidates VALUES(?,?,?,?,?)',(e['id'],now,price,'watch',json.dumps(detail)))
                    reason='paper_watch'
                elif row['status']=='watch':
                    age=now-row['first_at']
                    if age>240 or abs(price/row['price']-1)>.001:reason='paper_expired'
                    elif age>=30:
                        existing=self.book.db.execute('SELECT 1 FROM signals WHERE id LIKE ? LIMIT 1',('%:'+e['id'],)).fetchone()
                        active=self.book.db.execute("SELECT plan FROM signals WHERE status='open' AND policy='levels'").fetchall()
                        if any(json.loads(x['plan'])['strategy']==e['strategy'] for x in active):reason='paper_strategy_already_open'
                        else:
                            if not existing:
                                frozen=dict(plan,data_source=SOURCE,public_techniques=dict(version=VERSION,event=e,baseline_reason=baseline_reason))
                                self.book.add(e['id'],frozen,now,**self.costs)
                            with self.book.db:self.book.db.execute("UPDATE technique_candidates SET status='opened' WHERE id=?",(e['id'],))
                            reason='paper_opened' if not existing else 'already_recorded'
                    else:reason='paper_watch'
                    if reason=='paper_expired':
                        with self.book.db:self.book.db.execute("UPDATE technique_candidates SET status='expired' WHERE id=?",(e['id'],))
                else:reason='already_recorded'
            decisions.append(dict(id=e['id'],strategy=e['strategy'],reason=reason))
        # Only compact decisions; no full candles/history copied each minute.
        study_reason=(ctx.get('reason') if not ctx.get('ready') else
                      'ready_no_setup' if not ctx.get('setups') else 'setup_evaluated')
        self.book.sample(now,price,decision=dict(reason='public_technique_paper',decisions=decisions,
            baseline_reason=baseline_reason,study_context=dict(ready=ctx.get('ready',False),
            hourly_ready=ctx.get('hourly_ready',False),reason=study_reason)))
        with self.book.db:
            self.book.db.execute('DELETE FROM technique_candidates WHERE first_at<?',(now-7*86400,))
        return dict(version=VERSION,ready=ctx['ready'],hourly_ready=ctx.get('hourly_ready',False),
                    setups=len(ctx['setups']),decisions=decisions,outcomes=outcomes,
                    live_gate=False,live_entry_added=False)

    def summary(self):
        result={}
        for strategy in LABELS:
            selected=[r for r in self.book.db.execute("SELECT * FROM signals WHERE policy='levels'") if json.loads(r['plan'])['strategy']==strategy]
            done=[r for r in selected if r['net'] is not None]
            result[strategy]=dict(entries=len(selected),completed=len(done),wins=sum(r['net']>0 for r in done),
                censored=sum(r['status'].startswith('censored_') for r in selected),
                mean_net_usdt=sum(r['net'] for r in done)/len(done) if done else None)
        return result

    def report_line(self):
        coverage=self.book.db.execute('SELECT MIN(ts),MAX(ts),COUNT(*) FROM samples').fetchone()
        span=('자료 없음' if not coverage[2] else
              f"{datetime.fromtimestamp(coverage[0],KST):%m-%d %H:%M}~{datetime.fromtimestamp(coverage[1],KST):%m-%d %H:%M} KST")
        reasons=audit.study_diagnostics(self.book.db)
        lines=['\n[공개기법 모의검증·최대 90일 보관·90일 백테스트 아님]',
               f'실제 보관 평가범위 {span} · 연속성 미검증',
               '자료상태 '+(' · '.join(f'{k} {v}' for k,v in sorted(reasons.items())) or '기록 없음')]
        for name,row in self.summary().items():
            mean='미집계' if row['mean_net_usdt'] is None else f"{row['mean_net_usdt']:+.2f} USDT"
            lines.append(f"{LABELS[name]}: 진입 {row['entries']} · 완료 {row['completed']} · 공백 {row['censored']} · 평균 {mean}")
        lines.append('실험 간 중복 노출 가능·계좌수익 아님·기존 알람 조건 유지')
        return '\n'.join(lines)
