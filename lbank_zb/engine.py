"""One clocked engine for offline and externally-supplied native events.
No running collector or transport is started by importing or executing this module.
"""
from .core import *

class Engine:
    def __init__(self,costs,latency=60,strategies=None):
        if latency not in (60,120):raise ValueError('scenario latency')
        self.costs=costs;self.latency=latency;self.strategies=set(strategies or ['TREND_PULLBACK','BREAKOUT_RETEST','FAILED_BREAK_RECLAIM'])
        self.one=[];self.five=[];self.fifteen=[];self.watches={};self.pending=[];self.position=None;self.closed=[]
        self.equity=D(1000);self.peak=D(1000);self.day=None;self.day_start=D(1000);self.review=False;self.health=set()
        self.events=[];self.seen=set();self.last_exit=0;self.counts={};self.daily={};self.clock=0
    def emit(self,kind,now,**data):
        self.counts[kind]=self.counts.get(kind,0)+1;self.events.append({'kind':kind,'time':now,**data})
    def nav(self,price=None):
        return self.equity+(self.position.mark(price or self.position.last_price) if self.position and self.position.state!='PAPER_CLOSED' else ZERO)
    def day_update(self,now,price):
        day=(now+9*3600)//86400;nav=self.nav(price)
        if self.day!=day:
            if self.day is not None:self.daily[str(self.day)]={'start':str(self.day_start),'end':str(nav),'return':str((nav-self.day_start)/self.day_start)}
            self.day=day;self.day_start=nav
        self.peak=max(self.peak,nav)
        if nav<=self.peak*D('.95'):self.review=True
    def finish_position(self,now):
        if self.position and self.position.state=='PAPER_CLOSED':
            pos=self.position;self.equity+=pos.cash;self.closed.append(pos);self.last_exit=now;self.position=None
    def ingest(self,b,now,quote=None):
        b.validate(now);self.clock=now
        key=f'{b.period}:{b.start}'
        if key in self.seen:return
        self.seen.add(key);self.day_update(now,b.c)
        if b.period==60:
            if self.one and b.start!=self.one[-1].end:
                self.health.add('DATA_DEGRADED')
                if self.position:self.position.health.add('DATA_CENSORED')
            if self.one and b.start<=self.one[-1].start:raise ValueError('OUT_OF_ORDER')
            self.one=(self.one+[b])[-60:]
            # Pending entry uses the first minute open at or beyond the fixed latency.
            for c in list(self.pending):
                if b.start<c.signal_time+self.latency:
                    if b.start>=c.signal_time and (b.l<=c.S if c.side==1 else b.h>=c.S):
                        self.pending.remove(c);self.emit('CANCELLED',now,id=c.id,reason='STOP_BEFORE_FILL')
                    continue
                self.pending.remove(c)
                if b.start>c.expires:self.emit('EXPIRED',now,id=c.id);continue
                if self.position:self.emit('SUPPRESSED_OVERLAP',now,id=c.id);continue
                if 'DATA_DEGRADED' in self.health:self.emit('DATA_DEGRADED',now,id=c.id);continue
                if c.origin<=self.last_exit:self.emit('CANCELLED',now,id=c.id,reason='NEW_PATTERN_REQUIRED');continue
                nav=self.nav(b.o)
                if self.review or nav<=self.day_start*D('.99'):self.emit('RISK_HALT',now,id=c.id);continue
                if c.side*(b.o-c.I)<=0 or c.side*(b.o-c.S)<=0:self.emit('CANCELLED',now,id=c.id,reason='THESIS_CROSSED_BEFORE_FILL');continue
                # Reuse levels known at signal, not subsequently formed pivots.
                levels=[p['price'] for p in pivots(self.five,c.signal_time) if p['index']>=len(self.five)-120 and p['side']==('high' if c.side==1 else 'low')]
                p,why=plan(c,b.o,self.costs,nav,levels)
                if why:self.emit('UNFILLED',now,id=c.id,reasons=why);continue
                if p.risk>nav*D('.005'):self.emit('RISK_HALT',now,id=c.id);continue
                self.position=Position.open(p,b.start);self.emit('PAPER_OPEN',b.start,id=p.id,plan=p)
            if self.position:
                n=len(self.position.events);self.position.minute(b,now)
                for ev in self.position.events[n:]:self.emit(ev['kind'],ev['time'],id=self.position.plan.id,detail=ev)
                self.finish_position(now)
        elif b.period==300:
            if self.five and b.start<=self.five[-1].start:raise ValueError('OUT_OF_ORDER')
            if self.five and b.start!=self.five[-1].end:
                self.health.add('DATA_DEGRADED');self.five=[];self.fifteen=[];self.watches={};self.pending=[]
                if self.position:self.position.health.add('DATA_CENSORED')
            self.five=(self.five+[b])[-600:]
            # Native five minute bars cross-checked against complete one minute group.
            minutes=[x for x in self.one if b.start<=x.start<b.end]
            agg=aggregate(minutes,300) if contiguous(minutes) else []
            if not agg or not self.costs.tick or any(abs(getattr(agg[0],k)-getattr(b,k))>self.costs.tick for k in ('o','h','l','c')):
                self.health.add('DATA_DEGRADED');self.emit('BAR_MISMATCH_OR_UNIT_UNKNOWN',now,bar=b.start)
            elif abs(agg[0].v-b.v)>D('.00000001'):
                self.health.add('DATA_DEGRADED');self.emit('VOLUME_MISMATCH',now,bar=b.start)
            self.fifteen=aggregate(self.five,900)[-160:]
            state=regime(self.fifteen) if contiguous(self.fifteen) else 'UNKNOWN'
            self.emit('REGIME',now,value=state)
            if self.position:
                a=features(self.five)['atr'][-1]
                if a:
                    n=len(self.position.events);self.position.five(b,a,state,now)
                    for ev in self.position.events[n:]:self.emit(ev['kind'],ev['time'],id=self.position.plan.id,detail=ev)
                    self.finish_position(now)
            if len(self.five)<150 or len(self.fifteen)<150 or len(self.one)<60 or state=='UNKNOWN':self.emit('WARMUP',now);return
            # Recovery only after an entire fresh contiguous warm-up has been restored.
            if contiguous(self.five[-450:]) and len(agg)==1 and self.costs.tick and all(abs(getattr(agg[0],k)-getattr(b,k))<=self.costs.tick for k in ('o','h','l','c')) and agg[0].v==b.v:self.health.discard('DATA_DEGRADED')
            candidates,watch_events=detect(self.five,state,self.watches)
            for e in watch_events:self.emit(e['type'],now,detail=e)
            plans=[];mapping={};levels=pivots(self.five,b.end)
            for c in candidates:
                if c.strategy not in self.strategies:continue
                self.emit('DETECTED',now,id=c.id,thesis=c,features={'rsi':features(self.five)['rsi'][-1],'divergence':divergence(self.five,b.end),'BTC_D':'UNKNOWN','USDT_D':'UNKNOWN','USDT_USD':'UNKNOWN'})
                why=[]
                if now>c.expires:why.append('STALE_SIGNAL')
                if c.origin<=self.last_exit:why.append('NEW_PATTERN_REQUIRED')
                if self.position:why.append('ACTIVE_POSITION')
                if self.health or not bar_fresh(self.one,60,now) or not bar_fresh(self.five,300,now):why.append('DATA_DEGRADED')
                nav=self.nav(b.c)
                if self.review or nav<=self.day_start*D('.99'):why.append('RISK_HALT')
                ahead=[v['price'] for v in levels if v['index']>=len(self.five)-120 and v['side']==('high' if c.side==1 else 'low')]
                p,econ=plan(c,c.close,self.costs,nav,ahead);why+=econ
                if why:self.emit('REJECTED',now,id=c.id,reasons=sorted(set(why)));continue
                plans.append(p);mapping[p.id]=c
            chosen,suppressed=select(plans)
            for id,reason in suppressed.items():self.emit(reason,now,id=id)
            if chosen:
                self.pending.append(mapping[chosen.id]);self.emit('READY',now,id=chosen.id,message=render(chosen),expires=chosen.candidate.expires)
        else:raise ValueError('ingest raw1m then native5m only;15m internally derived')
    def quote_ready(self,quote,now,candidate):
        # Connection hook deliberately fails closed unless native normalized quantity-aware quote supplied.
        if quote is None:return False
        return (quote.get('product')=='lbank:linear_usdt_perpetual:BTCUSDT:LAST' and quote.get('quantity_btc') is not None and quote.get('depth_verified') is True and 0<=now-quote['received']<=5 and now<=candidate.expires and candidate.band[0]<=dec(quote['fill_price'])<=candidate.band[1])
    def report(self):
        results=[p for p in self.closed if not p.health];unknown=[p for p in self.closed if p.health]
        nets=[p.cash for p in results];wins=sum((max(x,ZERO) for x in nets),ZERO);loss=-sum((min(x,ZERO) for x in nets),ZERO)
        blocked=bool(unknown or self.health or (self.position and self.position.health) or self.costs.status!='VERIFIED')
        return {'spec_version':'ZB-1.0','counts':self.counts,'closed':len(self.closed),'verified_closed':len(results) if not blocked else 0,'unresolved_closed':len(unknown),'open_at_cutoff':int(self.position is not None),'model_net_excluding_unresolved':sum(nets,ZERO),'model_net_all_closed':sum((p.cash for p in self.closed),ZERO),'model_nav':self.nav(),'verified_net':None,'PF_model':wins/loss if loss else None,'funding_status':self.costs.status,'classification':'EVIDENCE_INSUFFICIENT','health':sorted(self.health),'performance_claim':False,'baseline_comparison':None,'daily':self.daily,'limitations':['Native cost provenance/funding coverage required for verified performance.','No sealed historical data or normalized existing-baseline comparison automatically implied.','Funding coverage certification is not yet wired: verified_net stays NULL even when a scenario is labelled VERIFIED.','Favorable intrabar bound and grouped-block uncertainty report remain separate unfinished evaluation work.']}
