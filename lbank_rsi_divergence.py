"""Completed-bar RSI context and independent paper study; NEVER an entry veto.

Explicit operational hypotheses, not a reproduction of a proprietary indicator.
Price pivots need two bars on BOTH sides. Signals are dated at discovery, never
backdated onto the pivot. Only native contiguous completed candles are accepted.
"""
import json
import math

from lbank_native_entry import wilder_rsi_series, SOURCE
from lbank_performance import PerformanceBook
from lbank_trend_runner import atr_wilder
import lbank_performance_audit as audit

VERSION = "rsi_context_v1"


def valid_bars(bars, period):
    if not bars:
        return False
    for i, b in enumerate(bars):
        if (b.get("quality") != "stream_closed" or b["start"] % period
                or (i and b["start"]-bars[i-1]["start"] != period)):
            return False
        o, h, l, c = (b[k] for k in ("open", "high", "low", "close"))
        if (not all(math.isfinite(x) and x > 0 for x in (o,h,l,c))
                or l > min(o,c) or h < max(o,c) or l > h):
            return False
    return True


def pivots(bars, key):
    """Strict extrema avoid choosing an arbitrary point on equal-price plateaus."""
    result = []
    for i in range(2, len(bars)-2):
        value = bars[i][key]
        others = [bars[j][key] for j in (i-2,i-1,i+1,i+2)]
        if all(value < x for x in others) if key == "low" else all(value > x for x in others):
            result.append(i)
    return result


def analyze(state):
    """Public boundary: absence/corruption removes annotation, never the alert."""
    try:
        return _analyze(state)
    except (KeyError, TypeError, ValueError, OverflowError, IndexError):
        return {"version": VERSION, "ready": False, "reason": "invalid_native_data", "setups": []}


def _analyze(state):
    empty = {"version": VERSION, "ready": False, "setups": []}
    if not state or state.get("source") != SOURCE:
        return dict(empty, reason="native_data_unavailable")
    bars = state["five_bars"]
    if len(bars) < 30 or not valid_bars(bars, 300):
        return dict(empty, reason="insufficient_or_gapped_5m")
    rs = [None]*14+wilder_rsi_series([b["close"] for b in bars])
    atr = atr_wilder(bars)
    if not atr or not math.isfinite(atr):
        return dict(empty, reason="invalid_atr")
    lows, highs = pivots(bars, "low"), pivots(bars, "high")
    current = bars[-1]["close"]
    # A fixed, disclosed zone width; not optimized on the replay sample.
    zone = max(current*.0005, atr*.5)
    divergences, setups = [], []
    for direction, key, points in ((1,"low",lows),(-1,"high",highs)):
        points = [i for i in points if rs[i] is not None]
        if len(points) < 2:
            continue
        a, b = points[-2:]
        if not 3 <= b-a <= 24 or len(bars)-1-b > 6:
            continue
        price_a, price_b = bars[a][key], bars[b][key]
        # Regular divergence: price makes a new extreme, RSI does not.
        if direction*(price_a-price_b) < current*.0002 or direction*(rs[b]-rs[a]) < 2:
            continue
        invalidated = any(direction*(x[key]-price_b) < 0 for x in bars[b+1:])
        if invalidated:
            continue
        opposite = highs if direction == 1 else lows
        barriers = [bars[i]["high" if direction == 1 else "low"]
                    for i in opposite if i < b and direction*(bars[i]["high" if direction == 1 else "low"]-current)>0]
        barrier = (min(barriers) if direction == 1 else max(barriers)) if barriers else None
        event = {"strategy": "rsi_divergence", "direction": direction,
                 "id": f"div:{direction}:{bars[a]['start']}:{bars[b]['start']}",
                 "pivot1": bars[a]["start"], "pivot2": bars[b]["start"],
                 "available_at": bars[b+2]["start"]+300,
                 "rsi1": rs[a], "rsi2": rs[b], "level": price_a,
                 "structure_anchor": price_b, "barrier": barrier,
                 "near_prior_level": abs(price_b-price_a) <= zone,
                 "zone": zone}
        divergences.append(event)
        if event["near_prior_level"]:
            setups.append(event)
    # Separate trend-pullback hypothesis: HH/HL (or LH/LL), RSI rebound near
    # 50-60 (short 40-50). These levels apply ONLY to this paper candidate.
    if len(lows) >= 2 and len(highs) >= 2:
        for direction in (1,-1):
            trend = (direction*(bars[lows[-1]]["low"]-bars[lows[-2]]["low"]) > 0
                     and direction*(bars[highs[-1]]["high"]-bars[highs[-2]]["high"]) > 0)
            r0, r1 = rs[-2:]
            band = 50 <= r0 <= 60 if direction == 1 else 40 <= r0 <= 50
            impulse = max(rs[-12:-2]) >= 70 if direction == 1 else min(rs[-12:-2]) <= 30
            anchor_i = lows[-1] if direction == 1 else highs[-1]
            anchor = bars[anchor_i]["low" if direction == 1 else "high"]
            intact = all(direction*(x["low" if direction == 1 else "high"]-anchor)>=0
                         for x in bars[anchor_i+1:])
            if trend and band and impulse and direction*(r1-r0)>0 and intact and len(bars)-1-anchor_i<=12:
                barrier = bars[highs[-1]]["high"] if direction == 1 else bars[lows[-1]]["low"]
                setups.append({"strategy":"rsi_pullback", "direction":direction,
                    "id":f"pullback:{direction}:{bars[-1]['start']}",
                    "available_at":bars[-1]["start"]+300, "level":anchor,
                    "structure_anchor":anchor, "barrier":barrier, "zone":zone})
    return {"version":VERSION, "ready":True, "bar_start":bars[-1]["start"],
            "rsi":rs[-1], "rsi_above_50":rs[-1]>=50,
            "divergences":divergences, "setups":setups}


def confirmation(state, setup):
    """Fresh completed 1m body + reclaim/break, with no 50-line requirement."""
    bars = state.get("minute_bars", [])
    if len(bars) < 4 or not valid_bars(bars[-4:], 60):
        return False
    last, previous = bars[-1], bars[-4:-1]
    if last["start"]+60 < setup["available_at"]:
        return False
    d = setup["direction"]
    span = last["high"]-last["low"]
    if span <= 0 or d*(last["close"]-last["open"]) < span*.5:
        return False
    edge = (last["high"]-last["close"] if d==1 else last["close"]-last["low"])/span
    broken_anchor = any(d*(b["low" if d==1 else "high"]-setup["structure_anchor"])<0
                        for b in bars if b["start"] >= setup["available_at"])
    if edge > .25 or broken_anchor:
        return False
    breakout = (last["close"] > max(b["high"] for b in previous) if d==1
                else last["close"] < min(b["low"] for b in previous))
    reclaim = any(abs(b["low" if d==1 else "high"]-setup["level"]) <= setup["zone"] for b in previous)
    return d*(last["close"]-setup["level"])>0 and (breakout or reclaim)


def annotate(plan, state):
    """Only attach information AFTER original qualification; never mutate state."""
    try:
        context = analyze(state)
        if context["ready"]:
            relevant = context["divergences"]
            labels = []
            for event in relevant:
                side = "상승" if event["direction"]==1 else "하락"
                labels.append(side+" 다이버전스"+("·1분 반응" if confirmation(state,event) else "·반응 대기"))
            note = "/".join(labels) if labels else "다이버전스 미검출"
            return dict(plan, rsi_context=dict(context, label=note))
    except Exception:
        pass  # Optional metadata cannot cancel or delay an existing signal.
    return plan


class DivergenceStudy:
    """Independent journal/outcomes; no Telegram, no live cooldown/state access."""
    def __init__(self, path, balance):
        self.book = PerformanceBook(path, balance)
        self.book.db.execute("CREATE TABLE IF NOT EXISTS rsi_candidates(id TEXT PRIMARY KEY, first_at REAL, price REAL, status TEXT, detail TEXT)")

    def report_line(self):
        stats = self.book.summary()["levels"]
        mean = "미집계" if stats["expectancy_usdt"] is None else f"{stats['expectancy_usdt']:+.2f} USDT"
        reasons=audit.study_diagnostics(self.book.db)
        return (f"\n[RSI 별도 모의검증·누적] 후보진입 {stats['signals']} · 완료 {stats['closed']} · "
                f"자료공백 {stats['censored']} · 완료당 평균 순손익 {mean}\n"
                f"자료상태 {' · '.join(f'{k} {v}' for k,v in sorted(reasons.items())) or '기록 없음'}\n"
                "중복 포지션 가능·계좌수익 아님·기존 알람 차단 없음")

    def step(self, now, price, state, calculate_plan, context_gate, *, baseline_reason):
        outcomes = self.book.observe(now, price, native_state=state)
        context = analyze(state)
        decisions = []
        if context["ready"]:
            for setup in context["setups"]:
                d = setup["direction"]
                if not confirmation(state,setup):
                    decisions.append({"id":setup["id"],"reason":"await_1m_close"})
                    continue
                # Preserve native price freshness/context/cost gates for paper.
                reason = "qualified"
                if abs(price/state["prices"][-1]-1) > .001:
                    reason = "price_deviation"
                elif not context_gate(state,d,"range_rebound" if setup["strategy"]=="rsi_divergence" else "trend_retest")[0]:
                    reason = "context"
                elif len(state["rsi5"]["rsi"])<2 or d*(state["rsi5"]["rsi"][-1]-state["rsi5"]["rsi"][-2])<=0:
                    reason = "rsi_direction"
                plan = calculate_plan(state["prices"],d,setup,native_state=state) if reason=="qualified" else None
                if reason=="qualified" and plan is None:
                    reason = "risk_or_target"
                if plan is not None:
                    row = self.book.db.execute("SELECT * FROM rsi_candidates WHERE id=?",(setup["id"],)).fetchone()
                    if row is None:
                        with self.book.db:
                            self.book.db.execute("INSERT INTO rsi_candidates VALUES(?,?,?,?,?)",(setup["id"],now,price,"watch",json.dumps(setup)))
                        reason = "paper_watch"
                    elif row["status"]=="watch":
                        age = now-row["first_at"]
                        if age>240 or abs(price/row["price"]-1)>.001:
                            reason = "paper_expired"
                        elif age>=30:
                            # Recover a crash after add() committed but before the status update.
                            exists = self.book.db.execute("SELECT 1 FROM signals WHERE id LIKE ? LIMIT 1",("%:"+setup["id"],)).fetchone()
                            with self.book.db:
                                if not exists:
                                    self.book.add(setup["id"],dict(plan,runner_enabled=False,data_source=SOURCE),now)
                                self.book.db.execute("UPDATE rsi_candidates SET status='opened' WHERE id=?",(setup["id"],))
                            reason = "paper_opened"
                        else:
                            reason = "paper_watch"
                        if reason=="paper_expired":
                            with self.book.db:
                                self.book.db.execute("UPDATE rsi_candidates SET status='expired' WHERE id=?",(setup["id"],))
                    else:
                        reason = "already_recorded"
                decisions.append({"id":setup["id"],"strategy":setup["strategy"],"direction":d,"reason":reason})
        self.book.sample(now,price,decision={"reason":"rsi_shadow", "context":context,
                         "candidates":decisions,"baseline_reason":baseline_reason,
                         "study_context":{"ready":context.get("ready",False),
                           "reason":context.get("reason") if not context.get("ready") else
                           "ready_no_setup" if not context.get("setups") else "setup_evaluated"},
                         "minute_bar":state["minute_bars"][-1] if state else None})
        with self.book.db:
            self.book.db.execute("DELETE FROM rsi_candidates WHERE first_at<?",(now-7*86400,))
        return {"version":VERSION,"ready":context["ready"],"divergences":len(context.get("divergences",[])),
                "setups":len(context["setups"]),"decisions":decisions,"outcomes":outcomes,
                "live_gate":False,"live_entry_added":False}
