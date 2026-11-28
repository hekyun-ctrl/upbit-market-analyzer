"""Independent, read-only research planner. Never grants delivery or orders.
Parameters are hypotheses, not a fitted trading strategy. All levels precede
the decision; 30-minute first-touch outcomes exclude the partial entry minute.
"""
import math
from lbank_entry_quality import confirmed_pivot_barriers
from lbank_trend_runner import atr_wilder

POLICY = "integrated_structure_shadow_v1"
FEE, SLIP, FUNDING, MIN_RR = .0006, .0002, .0001, 2.0
HORIZON = 1800


def net(d, e, x, stop=False):
    x = x * (1-d*SLIP) if stop else x
    return d*(x-e)-FEE*(e+x)-FUNDING*e


def valid(rows, period, at, minimum):
    if not isinstance(rows, list) or len(rows) < minimum:
        return False
    prior = None
    for b in rows:
        try:
            s = b["start"]
            o,h,l,c = (float(b[k]) for k in ("open","high","low","close"))
            if (not isinstance(s,int) or s%period or s+period>at
                    or b["quality"]!="stream_closed" or not all(math.isfinite(x) and x>0 for x in (o,h,l,c))
                    or h<max(o,l,c) or l>min(o,h,c)
                    or (prior is not None and s!=prior+period)):
                return False
            prior=s
        except (KeyError, TypeError, ValueError, OverflowError):
            return False
    return rows[-1]["start"] == int(at//period)*period-period


class IntegratedShadow:
    def __init__(self):
        self.direction=0
        self.anchor=None
        self.last5=None
        self.phase="unresolved"
        self.pending={}
        self.seen={}
        self.last_minute=None

    def _advance(self, bars):
        b,a,z=bars[-1],bars[-2],bars[-3]
        c=b["close"]
        old=self.direction
        if old and old*(c-self.anchor)<=0:
            self.direction=0
            self.anchor=None
            self.phase="invalidated"
        up=c>max(a["high"],z["high"]) and c>a["close"]
        down=c<min(a["low"],z["low"]) and c<a["close"]
        d=1 if up else -1 if down else 0
        if d and d!=self.direction:
            self.direction=d
            self.anchor=min(x["low"] for x in bars[-3:]) if d==1 else max(x["high"] for x in bars[-3:])
            self.phase="transition"
        elif self.direction:
            d=self.direction
            # A confirmed local pivot can tighten, but never widen, the anchor.
            k="low" if d==1 else "high"
            if d*(a[k]-z[k])<0 and d*(a[k]-b[k])<0 and d*(c-a[k])>0:
                self.anchor=max(self.anchor,a[k]) if d==1 else min(self.anchor,a[k])
            self.phase="acceleration" if d*(c-a["close"])>0 and (up or down) else (
                "continuation" if d*(c-a["close"])>0 else "pullback")
        elif self.phase!="invalidated":
            self.phase="range"
        self.last5=b["start"]

    def _trend(self, five):
        if self.last5 is None or self.last5<five[0]["start"]:
            self.direction=0
            self.anchor=None
            self.phase="range"
            self.last5=None
        for i in range(2,len(five)):
            if self.last5 is None or five[i]["start"]>self.last5:
                self._advance(five[:i+1])

    def _plan(self, name, d, quote, one, five, atr1, atr5):
        prior=one[-4:-1]
        last=one[-1]
        level=max(b["high"] for b in prior) if d==1 else min(b["low"] for b in prior)
        # Actual closed-minute break or a held reclaim, not a forecast.
        trigger=d*(last["close"]-level)>0 and d*(last["close"]-last["open"])>0 and d*(quote-level)>0
        result=dict(strategy=name,direction=d,trigger=trigger,trigger_level=level,entry=quote,
                    stop=None,target=None,net_rr=None,eligible=False,approval=False)
        if not trigger:
            return dict(result,reason="no_closed_minute_trigger")
        swing=min(b["low"] for b in one[-5:]) if d==1 else max(b["high"] for b in one[-5:])
        floor=max(quote*.0005,2*atr1,atr5)
        stop=min(swing-quote*.0002,quote-floor) if d==1 else max(swing+quote*.0002,quote+floor)
        result.update(stop=stop,anchor=swing,noise_floor=floor)
        if abs(quote-stop)/quote>.006:
            return dict(result,reason="structural_stop_too_far")
        # Market levels FIRST: nearest known obstacle, including confirmed
        # pivots and recent unconfirmed extremes. No RR-derived target.
        levels=[b["price"] for b in confirmed_pivot_barriers(five,d,quote)]
        key="high" if d==1 else "low"
        levels.extend(b[key] for b in five[-6:] if d*(b[key]-quote)>0)
        if not levels:
            return dict(result,reason="target_room_unknown")
        barrier=min(levels) if d==1 else max(levels)
        target=barrier-d*quote*.00015
        loss=-net(d,quote,stop,True)
        rr=net(d,quote,target)/loss if loss>0 else None
        result.update(target=target,barrier=barrier,net_rr=rr)
        if d*(target-quote)<=0 or rr is None or rr<MIN_RR:
            return dict(result,reason="insufficient_market_room")
        if abs(quote-last["close"])/quote>.001:
            return dict(result,reason="quote_deviation")
        if d*(quote-level)>2.5*atr5:
            return dict(result,reason="extended_from_trigger")
        return dict(result,reason="shadow_qualified",eligible=True)

    def _forward(self, one, at):
        outcomes=[]
        for k,event in list(self.pending.items()):
            rows=[b for b in one if event["next"]<=b["start"]<event["end"]]
            for b in rows:
                if b["start"]!=event["next"]:
                    break
                d,e,s,t=(event[key] for key in ("direction","entry","stop","target"))
                stop=b["low"]<=s if d==1 else b["high"]>=s
                target=b["high"]>=t if d==1 else b["low"]<=t
                event["next"]+=60
                if stop or target:
                    kind="ambiguous_both" if stop and target else "stop" if stop else "target"
                    # Adverse gap at a stop is modeled at the worse open.
                    x=(min(s,b["open"]) if d==1 else max(s,b["open"])) if stop else t
                    outcomes.append(dict(event,outcome=kind,net_r=None if stop and target else
                        net(d,e,x,stop)/(-net(d,e,s,True)),execution_verified=False))
                    del self.pending[k]
                    break
                if event["next"]>=event["end"]:
                    outcomes.append(dict(event,outcome="timeout",net_r=net(d,e,b["close"])/(-net(d,e,s,True)),execution_verified=False))
                    del self.pending[k]
                    break
            if k in self.pending and at>=event["end"]+120:
                outcomes.append(dict(event,outcome="incomplete",net_r=None,execution_verified=False))
                del self.pending[k]
        return outcomes

    def step(self,state):
        base=dict(policy=POLICY,approval=False,delivery="unchanged",ready=False)
        if not state:
            return dict(base,reason="missing_state"),[]
        try:
            at=float(state["observed_at"]); quote=float(state["quote"])
            one=state["minute_bars"]; five=state["five_bars"]
            if (not math.isfinite(at) or not math.isfinite(quote) or quote<=0
                    or not valid(one,60,at,65) or not valid(five,300,at,20)):
                return dict(base,reason="invalid_or_gapped_bars"),[]
            atr1,atr5=atr_wilder(one),atr_wilder(five)
            if not atr1 or not atr5 or min(atr1,atr5)<=0:
                return dict(base,reason="atr_unavailable"),[]
            self._trend(five)
            outcomes=self._forward(one,at)
            # A is a distinct countertrend/fade hypothesis: failed local high
            # recovery with a bearish minute break. B and C follow structure.
            recent=five[-3:]
            fade=(recent[-1]["high"]<recent[-2]["high"] and recent[-1]["close"]<recent[-2]["close"])
            plans=[]
            for name,d,allowed in (("A",-1,fade),("B",-1,self.direction==-1),("C",1,self.direction==1)):
                plan=self._plan(name,d,quote,one,five,atr1,atr5) if allowed else dict(
                    strategy=name,direction=d,trigger=False,eligible=False,approval=False,reason="structure_not_aligned")
                plans.append(plan)
                # Also measure rejected, geometrically defined candidates,
                # not just the few allowed signals. At most one per side/route/5m.
                if plan.get("trigger") and plan.get("stop") and plan.get("target") and d*(plan["target"]-quote)>0:
                    key=(name,five[-1]["start"])
                    if key not in self.seen and len(self.pending)<128:
                        first=(int(at//60)+1)*60
                        self.pending[key]=dict(plan,first=first,next=first,end=first+HORIZON,decision_at=at)
                        self.seen[key]=at
            self.seen={k:v for k,v in self.seen.items() if at-v<7200}
            self.last_minute=one[-1]["start"]
            order=["C","A","B"] if self.direction==1 else ["B","A","C"] if self.direction==-1 else ["A","B","C"]
            return dict(base,ready=True,direction=self.direction,phase=self.phase,anchor=self.anchor,
                        bar=five[-1]["start"],minute=self.last_minute,quote=quote,
                        priority=order,plans=plans,pending=len(self.pending),reason="research_only"),outcomes
        except (KeyError,TypeError,ValueError,IndexError,OverflowError,ZeroDivisionError):
            return dict(base,reason="invalid_evidence"),[]
