"""Shadow-only 30-minute future-path audit of LBank pivot-barrier rejections.

Requires completed native 5m bars AFTER rejection. No execution inference.
Neither order permissions nor original stop, cost or target gates are changed.
Pending observations are lost on process restart; this is logged research.
"""
import math
from lbank_native_entry import SOURCE

POLICY = "barrier_rejection_30m_shadow_v1"


def evidence(state, direction, quote, pivot):
    """Distinguish an old pivot wick from a completed breakout and failed hold."""
    result = dict(policy=POLICY, status="unverified", verified=False)
    try:
        if state["source"] != SOURCE or direction not in (-1, 1):
            return result
        b = state["five_bars"][-1]
        if b["quality"] != "stream_closed" or b["start"] % 300:
            return result
        close = float(b["close"])
        if not all(math.isfinite(v) and v > 0 for v in (close, quote, pivot)):
            return result
        closed_above = direction*(close-pivot) > 0
        quote_above = direction*(quote-pivot) > 0
        status = ("closed_and_quote_beyond_pivot" if closed_above and quote_above
                  else "closed_breakout_quote_back_below" if closed_above
                  else "quote_only_unconfirmed_breakout" if quote_above
                  else "unbroken_pivot")
        return dict(result, status=status, verified=True,
                    last_completed_5m_start=b["start"],
                    last_completed_close=close, quote_beyond=quote_above,
                    completed_close_beyond=closed_above)
    except (KeyError,TypeError,IndexError,ValueError,OverflowError):
        return result


class BarrierAudit:
    """Fixed-horizon, read-only, first-touch audit; no actual order fills."""
    def __init__(self, horizon=1800, cap=128):
        if horizon < 300 or horizon % 300:
            raise ValueError("horizon must be 5m aligned")
        self.horizon = horizon
        self.cap = cap
        self.events = {}

    def record(self, state, direction, strategy, entry, stop, target, pivot):
        try:
            if (not state or state["source"] != SOURCE
                    or direction not in (-1, 1)
                    or not all(math.isfinite(float(x)) and float(x)>0
                               for x in (entry,stop,target,pivot))
                    or direction*(entry-stop)<=0 or direction*(target-entry)<=0):
                return None
            last = state["minute_bars"][-1]
            if last["quality"]!="stream_closed" or last["start"] % 60:
                return None
            closed_at=last["start"]+60
            first=(closed_at+299)//300*300
            key=f"{first}:{direction}:{strategy}:{pivot:.1f}"
            if key in self.events:
                return None
            if len(self.events)>=self.cap:
                return dict(policy=POLICY,status="capacity_full",approval=False)
            event=dict(policy=POLICY,id=key,first_future_5m_start=first,
                       direction=direction,strategy=strategy,
                       entry=float(entry),stop=float(stop),target1=float(target),
                       pivot=float(pivot),last_start=None,n=0,
                       future_high=None,future_low=None,first_reclaim_close=None,
                       pivot_evidence=evidence(state,direction,entry,pivot),
                       approval=False,execution_verified=False)
            self.events[key]=event
            return dict(event,status="tracking")
        except (KeyError,TypeError,ValueError,IndexError,OverflowError):
            return None

    def observe(self, state):
        if not state or state.get("source")!=SOURCE:
            return []
        try:
            bars={int(b["start"]):b for b in state["five_bars"]
                  if b.get("quality")=="stream_closed" and b["start"]%300==0}
        except (KeyError,TypeError,ValueError,OverflowError):
            return []
        if not bars:
            return []
        latest=max(bars)
        finished=[]
        for key,event in list(self.events.items()):
            first=event["first_future_5m_start"]
            next_start=(first if event["last_start"] is None
                        else event["last_start"]+300)
            outcome=None
            while next_start<=latest and next_start<first+self.horizon:
                b=bars.get(next_start)
                if b is None:
                    outcome="future_data_gap_unverified"
                    break
                try:
                    o,h,l,c=(float(b[k]) for k in ("open","high","low","close"))
                    if (not all(math.isfinite(x) and x>0 for x in (o,h,l,c))
                            or h<max(o,c,l) or l>min(o,c,h)):
                        outcome="invalid_future_bar_unverified"
                        break
                except (KeyError,TypeError,ValueError,OverflowError):
                    outcome="invalid_future_bar_unverified"
                    break
                event["n"]+=1
                event["last_start"]=next_start
                event["future_high"]=(h if event["future_high"] is None
                                      else max(h,event["future_high"]))
                event["future_low"]=(l if event["future_low"] is None
                                     else min(l,event["future_low"]))
                d=event["direction"]
                if event["first_reclaim_close"] is None and d*(c-event["pivot"])>0:
                    event["first_reclaim_close"]=next_start+300
                hit_t=(h>=event["target1"] if d==1 else l<=event["target1"])
                hit_s=(l<=event["stop"] if d==1 else h>=event["stop"])
                if hit_t and hit_s: outcome="same_bar_first_touch_ambiguous"
                elif hit_t: outcome="target1_first"
                elif hit_s: outcome="stop_first"
                if outcome: break
                next_start+=300
            if outcome is None and event["last_start"] is not None:
                if event["last_start"]+300>=first+self.horizon:
                    outcome="neither_first_hit_in_30m"
            if outcome:
                finished.append(dict(event,status=outcome,
                                     evaluated_minutes=event["n"]*5,
                                     first_touch_from_closed_5m_only=True,
                                     actual_fills_verified=False,profitability_verified=False))
                self.events.pop(key,None)
        return finished
