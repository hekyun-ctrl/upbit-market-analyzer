"""Deterministic closed-bar rules, Decimal accounting and immutable entry thesis.
All timestamps: integer UTC seconds. Prices LAST; quantities normalized BTC.
"""
from dataclasses import dataclass, field, asdict
from decimal import Decimal, ROUND_FLOOR, ROUND_CEILING
from pathlib import Path
import hashlib,json

D=Decimal
ZERO=D(0)
def dec(x):
    v=D(str(x))
    if not v.is_finite(): raise ValueError('nonfinite')
    return v
def encode(v):
    if isinstance(v,D):return str(v)
    if hasattr(v,'__dataclass_fields__'):return asdict(v)
    raise TypeError(type(v).__name__)
def dumps(v):return json.dumps(v,default=encode,sort_keys=True,separators=(',',':'),ensure_ascii=False)
def digest(v):return hashlib.sha256(dumps(v).encode()).hexdigest()
def config():return json.loads(Path(__file__).with_name('config.json').read_text())
def rounding(v,step,up=False):
    return (v/step).to_integral_value(rounding=ROUND_CEILING if up else ROUND_FLOOR)*step

@dataclass(frozen=True)
class Bar:
    start:int
    period:int
    o:D; h:D; l:D; c:D; v:D
    received:int
    closed:bool=True
    exchange:str='lbank'
    market_type:str='linear_usdt_perpetual'
    symbol:str='BTCUSDT'
    settlement_asset:str='USDT'
    price_basis:str='LAST'
    volume_unit:str='BTC'
    source:str='unverified'
    revision:int=0
    @property
    def end(self):return self.start+self.period
    def validate(self,now):
        if (self.exchange,self.market_type,self.symbol,self.settlement_asset,self.price_basis)!=('lbank','linear_usdt_perpetual','BTCUSDT','USDT','LAST'):
            raise ValueError('WRONG_PRODUCT')
        if self.period not in (60,300,900) or self.start%self.period:raise ValueError('TIME_BOUNDARY')
        if not all(x.is_finite() for x in (self.o,self.h,self.l,self.c,self.v)):raise ValueError('NONFINITE')
        if min(self.o,self.h,self.l,self.c)<=0 or self.v<0 or self.h<max(self.o,self.c,self.l) or self.l>min(self.o,self.c,self.h):raise ValueError('OHLC')
        if not self.closed or self.end>now or self.received>now or self.received<self.end:raise ValueError('NOT_AVAILABLE_CLOSED')
        if self.volume_unit!='BTC':raise ValueError('VOLUME_UNIT_UNKNOWN')

def contiguous(bars):
    return bool(bars) and all(b.period==bars[0].period and b.start==a.end for a,b in zip(bars,bars[1:]))
def aggregate(bars,period):
    if not bars or not contiguous(bars) or period%bars[0].period:raise ValueError('GAP')
    groups={}
    for b in bars:groups.setdefault(b.start//period*period,[]).append(b)
    out=[]
    for start,rows in sorted(groups.items()):
        if rows[0].start!=start or rows[-1].end!=start+period or len(rows)!=period//rows[0].period:continue
        if len({(b.exchange,b.market_type,b.symbol,b.price_basis,b.volume_unit) for b in rows})!=1:raise ValueError('MIXED_PRODUCT')
        out.append(Bar(start,period,rows[0].o,max(b.h for b in rows),min(b.l for b in rows),rows[-1].c,sum((b.v for b in rows),ZERO),max(b.received for b in rows),source='same_product_aggregation'))
    return out

def ema(values,n):
    out=[None]*len(values)
    if len(values)<n:return out
    out[n-1]=sum(values[:n],ZERO)/n;a=D(2)/D(n+1)
    for i in range(n,len(values)):out[i]=a*values[i]+(1-a)*out[i-1]
    return out
def features(bars):
    if not contiguous(bars):raise ValueError('GAP_RESET_REQUIRED')
    c=[b.c for b in bars];tr=[b.h-b.l if i==0 else max(b.h-b.l,abs(b.h-bars[i-1].c),abs(b.l-bars[i-1].c)) for i,b in enumerate(bars)]
    atr=[None]*len(bars);rsi=[None]*len(bars);er=[None]*len(bars)
    if len(bars)>=14:
        atr[13]=sum(tr[:14],ZERO)/14
        for i in range(14,len(bars)):atr[i]=(atr[i-1]*13+tr[i])/14
    if len(bars)>=15:
        changes=[c[i]-c[i-1] for i in range(1,len(c))];g=sum((max(x,ZERO) for x in changes[:14]),ZERO)/14;l=sum((max(-x,ZERO) for x in changes[:14]),ZERO)/14
        for i in range(14,len(c)):
            if i>14:g=(g*13+max(changes[i-1],ZERO))/14;l=(l*13+max(-changes[i-1],ZERO))/14
            rsi[i]=D(50) if g==l==0 else D(100) if l==0 else ZERO if g==0 else 100-100/(1+g/l)
    for i in range(12,len(c)):
        den=sum((abs(c[j]-c[j-1]) for j in range(i-11,i+1)),ZERO);er[i]=abs(c[i]-c[i-12])/den if den else ZERO
    return {'ema20':ema(c,20),'ema50':ema(c,50),'atr':atr,'rsi':rsi,'er':er}
def pivots(bars,now):
    out=[]
    for i in range(2,len(bars)-2):
        if bars[i+2].end>now or bars[i+2].received>now:continue
        neighbors=bars[i-2:i]+bars[i+1:i+3]
        if all(bars[i].h>b.h for b in neighbors):out.append({'side':'high','price':bars[i].h,'time':bars[i].start,'known_at':max(bars[i+2].end,bars[i+2].received),'index':i})
        if all(bars[i].l<b.l for b in neighbors):out.append({'side':'low','price':bars[i].l,'time':bars[i].start,'known_at':max(bars[i+2].end,bars[i+2].received),'index':i})
    return out
def regime(bars):
    if len(bars)<150:return 'UNKNOWN'
    f=features(bars);a=f['atr'][-1]
    if not a:return 'UNKNOWN'
    z=(f['ema20'][-1]-f['ema50'][-1])/a;s=(f['ema20'][-1]-f['ema20'][-4])/a
    if z>=D('.10') and s>=D('.05'):return 'TREND_UP'
    if z<=D('-.10') and s<=D('-.05'):return 'TREND_DOWN'
    return 'RANGE' if abs(z)<=D('.25') and f['er'][-1]<=D('.25') else 'TRANSITION'
def divergence(bars,now):
    f=features(bars);p=pivots(bars,now);out=[]
    for side,direction in [('low',1),('high',-1)]:
        rows=[v for v in p if v['side']==side]
        if len(rows)<2:continue
        a,b=rows[-2:];x,y=a['index'],b['index']
        if 3<=y-x<=30 and bars[-1].end-b['known_at']<=6*300 and f['rsi'][x] is not None and f['rsi'][y] is not None:
            if direction*(b['price']-a['price'])<0 and direction*(f['rsi'][y]-f['rsi'][x])>=3:out.append({'side':direction,'pivot_time':b['time'],'known_at':b['known_at']})
    return out

def mirrored(b):return Bar(b.start,b.period,-b.o,-b.l,-b.h,-b.c,b.v,b.received,source=b.source)
@dataclass(frozen=True)
class Candidate:
    strategy:str; side:int; signal_time:int; origin:int; close:D; atr:D; F:D; S:D; I:D
    box_target:D|None=None; box_far:D|None=None; watch_time:int=0
    @property
    def id(self):return f'ZB-1.0:BTCUSDT:{self.strategy}:{self.side}:{self.origin}'
    @property
    def expires(self):return self.signal_time+120
    @property
    def band(self):
        w=abs(self.close-self.S)*D('.15');return (self.close-w,self.close+w)

def detect(bars,state,watches):
    """Returns candidates and watch events. Caller processes each completed5m once."""
    if len(bars)<150:return [],[]
    f=features(bars);out=[];events=[];t=bars[-1];a=f['atr'][-2]
    if not a or a<=0:return [],[]
    for d in (1,-1):
        rows=bars if d==1 else [mirrored(b) for b in bars]
        c=rows[-1];prev=rows[-2];ef=f['ema20'] if d==1 else [None if x is None else -x for x in f['ema20']]
        clv=(c.c-c.l)/(c.h-c.l) if c.h!=c.l else D('.5')
        trend='TREND_UP' if d==1 else 'TREND_DOWN'
        def candidate(name,F,S,I,mid=None,far=None,watch=0):
            return Candidate(name,d,t.end,t.start,t.c,a,d*F,d*S,d*I,None if mid is None else d*mid,None if far is None else d*far,watch)
        if state==trend:
            was=any(rows[i].c>ef[i]+D('.30')*f['atr'][i] for i in range(len(rows)-4,len(rows)-1))
            if was and c.l<=ef[-2]+D('.15')*a and c.c>ef[-2] and c.c>prev.c and clv>=D('.60') and c.c-ef[-2]<=D('.80')*a:
                F=min(b.l for b in rows[-3:]);out.append(candidate('TREND_PULLBACK',F,F-D('.15')*a,F-D('.05')*a))
        w=watches.get(str(d))
        if w:
            u,wa=dec(w['U']),dec(w['A']);age=(t.start-w['start'])//300
            if age>3 or c.c<u-D('.35')*wa:
                events.append({'type':'EXPIRED' if age>3 else 'CANCELLED','watch':w});watches.pop(str(d),None)
            elif age>=1 and state in (trend,'TRANSITION') and c.l<=u+D('.15')*wa and c.l>=u-D('.35')*wa and c.c>u and clv>=D('.60'):
                x=candidate('BREAKOUT_RETEST',u,min(u-D('.35')*wa,c.l-D('.10')*wa),u-D('.20')*wa,watch=w['start']+300)
                out.append(Candidate(**{**asdict(x),'atr':wa}));watches.pop(str(d),None)
            elif age==3:
                events.append({'type':'EXPIRED','watch':w});watches.pop(str(d),None)
        u=max(b.h for b in rows[-25:-1]);lo=min(b.l for b in rows[-25:-1])
        if state in (trend,'TRANSITION') and str(d) not in watches and c.c>u+D('.10')*a and clv>=D('.70'):
            watches[str(d)]={'U':str(u),'D':str(lo),'A':str(a),'start':t.start,'side':d};events.append({'type':'WATCHING','watch':watches[str(d)]})
        if state in ('RANGE','TRANSITION') and c.l<lo-D('.10')*a and c.c>lo+D('.05')*a and clv>=D('.65'):
            out.append(candidate('FAILED_BREAK_RECLAIM',lo,c.l-D('.15')*a,lo-D('.05')*a,(u+lo)/2,u))
    return out,events

@dataclass(frozen=True)
class Costs:
    fee_entry:D|None; fee_exit:D|None; slip_bps:D
    tick:D|None; step:D|None; minimum_notional:D|None
    funding_per_btc:D|None
    status:str='ASSUMED'
    version:str='unverified'
    def known(self):return all(x is not None for x in (self.fee_entry,self.fee_exit,self.tick,self.step,self.minimum_notional,self.funding_per_btc))
    def fill(self,price,side):return price*(1+D(side)*self.slip_bps/10000)

def planning_funding(now,schedule,displayed_rate,history,price):
    if schedule is None or displayed_rate is None:return None
    prior=[abs(dec(e['rate'])) for e in history if e['known_at']<=now and now-30*86400<=e['time']<=now]
    rate=max([abs(displayed_rate)]+prior)
    return price*rate*sum(now<event<=now+6*3600 for event in schedule)

@dataclass(frozen=True)
class Plan:
    candidate:Candidate; entry:D; stop:D; target:D; reference_target:D; quantity:D
    risk:D; rr:D; fees_funding:D; costs:Costs; equity:D
    @property
    def id(self):return self.candidate.id

def plan(c,price,costs,equity,levels=(),qty_risk=D('.0025'),entry_fill=None):
    reasons=[];d=c.side
    if not costs.known():return None,['COST_OR_PRODUCT_UNKNOWN']
    if min(costs.tick,costs.step)<=0 or min(costs.fee_entry,costs.fee_exit,costs.slip_bps,costs.minimum_notional,costs.funding_per_btc)<0:raise ValueError('COST_METADATA')
    stop=rounding(c.S,costs.tick,up=d<0);entry=costs.fill(price,d) if entry_fill is None else dec(entry_fill)
    if not c.band[0]<=entry<=c.band[1]:reasons.append('ENTRY_RANGE')
    if d*(entry-stop)<=0 or d*(entry-c.I)<=0:reasons.append('STRUCTURE_ORDER')
    dist=abs(entry-stop);target=entry+d*D('1.5')*dist;ref=entry+d*3*dist
    ahead=[x for x in levels if d*(x-entry)>0]
    if ahead:
        near=min(ahead,key=lambda x:abs(x-entry))
        if d*(near-target)<0:target=near-d*D('.05')*c.atr
    if c.box_target is not None and d*(c.box_target-target)<0:target=c.box_target
    if c.box_far is not None:ref=c.box_far
    target=rounding(target,costs.tick,up=d<0)
    loss_price=costs.fill(stop,-d);target_fill=costs.fill(target,-d)
    loss=d*(entry-loss_price)+entry*costs.fee_entry+loss_price*costs.fee_exit+costs.funding_per_btc
    gain=d*(target_fill-entry)-entry*costs.fee_entry-target_fill*costs.fee_exit-costs.funding_per_btc
    expense=entry*costs.fee_entry+target_fill*costs.fee_exit+costs.funding_per_btc+abs(price-entry)+abs(target-target_fill)
    if loss<=0:return None,reasons+['INVALID_RISK']
    rr=gain/loss
    if rr<D('1.20'):reasons.append('TARGET_NET_RR')
    if expense/loss>D('.20'):reasons.append('COST_TOO_LARGE')
    q=rounding(equity*qty_risk/loss,costs.step)
    if q<=0 or q*entry<costs.minimum_notional:reasons.append('MINIMUM_ORDER')
    if reasons:return None,sorted(set(reasons))
    return Plan(c,entry,stop,target,ref,q,q*loss,rr,expense*q,costs,equity),[]

def select(plans):
    if len({p.candidate.side for p in plans})>1:return None,{p.id:'CONFLICT' for p in plans}
    if not plans:return None,{}
    rank={'TREND_PULLBACK':0,'BREAKOUT_RETEST':1,'FAILED_BREAK_RECLAIM':2}
    ordered=sorted(plans,key=lambda p:(-p.rr,p.candidate.watch_time or p.candidate.signal_time,rank[p.candidate.strategy],p.id))
    return ordered[0],{p.id:'SUPPRESSED_OVERLAP' for p in ordered[1:]}

@dataclass
class Position:
    plan:Plan; fill_time:int; remaining:D; cash:D; active_stop:D; stop_since:int
    state:str='PAPER_OPEN'; partial:bool=False; funding:D=ZERO
    last_price:D|None=None; events:list=field(default_factory=list); health:set=field(default_factory=set)
    last_minute:int|None=None; high:D|None=None; low:D|None=None
    review:set=field(default_factory=set); warned:bool=False; last_trail_sent:D|None=None
    remaining_funding:D=ZERO;partial_skipped:bool=False
    @classmethod
    def open(cls,p,now):return cls(p,now,p.quantity,-p.quantity*p.entry*p.costs.fee_entry,p.stop,now,last_price=p.entry)
    def exit(self,price,q,now,reason):
        p=self.plan;fill=p.costs.fill(price,-p.candidate.side)
        if q<=0 or q>self.remaining:raise ValueError('EXIT_QUANTITY')
        self.remaining_funding*=1-q/self.remaining
        self.cash+=p.candidate.side*q*(fill-p.entry)-q*fill*p.costs.fee_exit;self.remaining-=q;self.last_price=fill
        self.events.append({'kind':reason,'time':now,'price':str(fill),'quantity':str(q)})
        if self.remaining==0:self.state='PAPER_CLOSED'
    def funding_event(self,time,rate,price):
        if self.state=='PAPER_CLOSED':return
        paid=self.plan.candidate.side*self.remaining*price*rate;self.cash-=paid;self.funding+=paid;self.remaining_funding+=paid
        self.events.append({'kind':'FUNDING','time':time,'paid':str(paid)})
    def mark(self,price):
        p=self.plan;fill=p.costs.fill(price,-p.candidate.side)
        return self.cash+p.candidate.side*self.remaining*(fill-p.entry)-self.remaining*fill*p.costs.fee_exit
    def minute(self,b,now):
        if self.state=='PAPER_CLOSED':return
        if b.start<self.fill_time:
            self.health.add('AMBIGUOUS_INTRABAR');return
        if self.last_minute is not None and b.start!=self.last_minute+60:self.health.add('DATA_CENSORED')
        self.last_minute=b.start;p=self.plan;d=p.candidate.side;self.last_price=b.c
        stop_hit=b.l<=self.active_stop if d==1 else b.h>=self.active_stop
        target_hit=b.h>=p.target if d==1 else b.l<=p.target
        if self.stop_since>b.start:
            self.health.add('AMBIGUOUS_INTRABAR');return
        if stop_hit:
            if target_hit and not self.partial:self.health.add('AMBIGUOUS_INTRABAR')
            px=min(self.active_stop,b.o) if d==1 else max(self.active_stop,b.o)
            self.exit(px,self.remaining,now,'HARD_STOP');return
        if target_hit and not self.partial:
            q=rounding(p.quantity*D('.25'),p.costs.step)
            fill=p.costs.fill(p.target,-d);remaining=self.remaining-q
            if q>0 and remaining>0 and q*fill>=p.costs.minimum_notional and remaining*fill>=p.costs.minimum_notional:
                self.exit(p.target,q,now,'PARTIAL_TP1');self.events[-1]['actual_fraction']=str(q/p.quantity)
                self.partial=True;self.state='PAPER_PARTIAL'
            elif not self.partial_skipped:
                self.partial_skipped=True;self.events.append({'kind':'PARTIAL_UNEXECUTABLE','time':now,'reason':'STEP_OR_MINIMUM_ORDER','desired_fraction':'0.25','actual_fraction':'0'})
        for hours in (6,24):
            if now-self.fill_time>=hours*3600 and hours not in self.review:self.review.add(hours);self.events.append({'kind':'TIME_REVIEW','hours':hours,'time':now})
    def five(self,b,atr,state,now):
        if self.state=='PAPER_CLOSED' or b.start<self.fill_time:return
        p=self.plan;d=p.candidate.side
        if d*(b.c-p.candidate.I)<0:self.exit(b.c,self.remaining,now,'STRUCTURE_INVALIDATED');return
        self.high=max(self.high or b.h,b.h);self.low=min(self.low or b.l,b.l)
        if self.partial:
            # Entry fee per BTC stays allocated; funding is only that of remaining lots.
            per=p.entry*p.costs.fee_entry+self.remaining_funding/self.remaining
            exit_fill_be=(p.entry+d*per)/(1-d*p.costs.fee_exit)
            trigger=exit_fill_be/(1-d*p.costs.slip_bps/10000)
            # Favorable tick direction is required to preserve nonnegative residual net.
            be=rounding(trigger,p.costs.tick,up=d>0)
            residual=lambda x:d*(p.costs.fill(x,-d)-p.entry)-p.entry*p.costs.fee_entry-p.costs.fill(x,-d)*p.costs.fee_exit-self.remaining_funding/self.remaining
            if residual(be)<0:be+=d*p.costs.tick
            if residual(be)<0:raise ValueError('BREAKEVEN_ROUNDING')
            trail=self.high-2*atr if d==1 else self.low+2*atr
            new=max(self.active_stop,be,trail) if d==1 else min(self.active_stop,be,trail)
            new=rounding(new,p.costs.tick,up=d<0)
            if d*(new-self.active_stop)>0:
                old=self.active_stop;self.active_stop=new;self.stop_since=now
                if d*(b.c-new)<=0:self.exit(b.c,self.remaining,now,'TRAIL_ALREADY_CROSSED');return
                base=self.last_trail_sent if self.last_trail_sent is not None else old
                if abs(new-base)>=D('.25')*p.candidate.atr or d*(old-be)<0<=d*(new-be):self.last_trail_sent=new;self.events.append({'kind':'TRAIL_UPDATED','time':now,'stop':str(new)})
        if state in ('TRANSITION','RANGE') and not self.warned:self.warned=True;self.events.append({'kind':'CAUTION','time':now,'regime':state})

def bar_fresh(bars,period,now,grace=15):
    expected=now//period*period
    if now%period<=grace:expected-=period
    return bool(bars) and bars[-1].end>=expected

def render(p):
    c=p.candidate;lo,hi=c.band
    from datetime import datetime,timezone,timedelta
    fmt=lambda t:datetime.fromtimestamp(t,timezone(timedelta(hours=9))).isoformat()
    names={'TREND_PULLBACK':'추세 되돌림','BREAKOUT_RETEST':'돌파 후 재지지','FAILED_BREAK_RECLAIM':'박스 이탈 실패 후 복귀'}
    return (f'[모의 검증 | BTCUSDT 선물 | {"LONG" if c.side==1 else "SHORT"} | {names[c.strategy]}]\n'
            f'판정 {fmt(c.signal_time)} / 데이터 {fmt(c.signal_time)} / 만료 {fmt(c.expires)}\n'
            f'진입 범위 {lo}~{hi} / 체결 추정 {p.entry}\n손절 {p.stop} LAST / 5분 종가 무효화 {c.I}\n'
            f'1차 {p.target}: 25% / 잔여75% ATR2 추적 / 다음 참고 {p.reference_target}\n'
            f'가상 시드 {p.equity} USDT / 계획 위험 {p.risk} USDT(최대0.25%) / 참고 BTC {p.quantity}\n'
            f'비용후 목표 손익비 {p.rr} / 비용 {p.costs.status}:{p.costs.version}\n'
            f'근거 {names[c.strategy]} / 상충·자료 상태는 사건 기록 참조\n{c.id} / ZB-1.0')
