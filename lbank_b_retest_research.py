"""Independent B-short failed-reclaim research + directional A/B/C ranking.

All live calculations use completed, contiguous native LBank-derived 5m bars.
No network, orders, or Telegram calls. The shadow candidate is NOT a live
entry signal. P1 targets must be observable and causal, never extrapolated.
"""
from collections import Counter
from datetime import datetime, timedelta, timezone
from hashlib import sha256
import json
import logging
import math
from statistics import median

from lbank_structural_target_plan import available_barriers, entry_bound, rounded
from research.zerobase.video_entry import Costs, judge_entry

LOG = logging.getLogger("lbank_b_retest_research")
POLICY = "b_failed_reclaim_shadow_v1"
MIN_HISTORY = 96
MIN_RR = 2.0
LOOKBACK_BREAK = 6
MAX_CONFIRM_BARS = 3
KST = timezone(timedelta(hours=9))


def _valid(bars):
    if not bars:
        return False
    prev = None
    for b in bars:
        try:
            start = b["start"]
            if (type(start) is not int or start % 300
                    or prev is not None and start != prev + 300
                    or b.get("quality") != "stream_closed"
                    or b.get("period", 300) != 300):
                return False
            prev = start
            o, h, l, c, v = [float(b[k]) for k in
                             ("open", "high", "low", "close", "volume")]
            receipt = b.get("received_at")
            if (not all(map(math.isfinite, (o, h, l, c, v)))
                    or l <= 0 or v < 0 or not l <= min(o, c) <= max(o, c) <= h
                    or type(receipt) not in (float, int)
                    or not math.isfinite(receipt) or receipt < start+300):
                return False
        except (ValueError, KeyError, TypeError, OverflowError):
            return False
    return True


def _ema(values, length):
    e, weight = float(values[0]), 2/(length+1)
    for v in values[1:]:
        e += weight * (float(v)-e)
    return e


def _aggregate(bars, period):
    """Take only fully observed, aligned 15m/1h/4h groups."""
    n = period//300
    blocks = []
    bucket, group = None, []
    for bar in bars:
        start = bar["start"]//period*period
        if bucket != start:
            if bucket is not None and len(group) == n and group[0]["start"] == bucket:
                blocks.append(group[-1]["close"])
            bucket, group = start, [bar]
        else:
            group.append(bar)
    if bucket is not None and len(group) == n and group[0]["start"] == bucket:
        blocks.append(group[-1]["close"])
    return blocks


def trend_priority(bars):
    """15m/1h consensus first; completed 5m defines market phase.

    4h direction is a weak reference only; never claim 4h EMA warmup from
    a 240-bar 5m window. Insufficient/contradicting timeframes -> neutral.
    """
    if len(bars) < MIN_HISTORY:
        return dict(status="insufficient_history", trend="unknown",
                    priorities=["관망"], reason="need_96_contiguous_5m")
    rows = bars[-min(len(bars), 240):]
    if not _valid(rows):
        return dict(status="invalid_source", trend="unknown",
                    priorities=["관망"], reason="invalid_or_gap")
    q15, q1h, q4h = (_aggregate(rows, p) for p in (900, 3600, 14400))
    if len(q15) < 12 or len(q1h) < 6:
        return dict(status="insufficient_higher_timeframe", trend="unknown",
                    priorities=["관망"], reason="closed_groups_missing")
    c5 = [b["close"] for b in rows]
    d5 = -1 if c5[-1] < _ema(c5, 21) else 1 if c5[-1] > _ema(c5, 21) else 0
    d15 = -1 if _ema(q15, 4) < _ema(q15, 9) else 1
    d1h = -1 if _ema(q1h, 3) < _ema(q1h, 6) else 1
    if d15 == d1h == -1:
        trend = "bearish"
        prev_support = min(float(x["low"]) for x in rows[-7:-1])
        # A fresh support break or lower 5m position gives B priority.
        b_first = c5[-1] <= prev_support or (
            c5[-1] < _ema(c5, 9) and d5 == -1)
        priorities = (["1순위 B-숏 (지지 이탈·재회복 실패)", "2순위 A-숏 (반등 저항 실패)",
                       "C-롱은 별도 추세 전환 확인 후"] if b_first else
                      ["1순위 A-숏 (반등 저항 실패)", "2순위 B-숏 (지지 이탈·재회복 실패)",
                       "C-롱은 별도 추세 전환 확인 후"])
    elif d15 == d1h == 1:
        trend = "bullish"
        priorities = ["1순위 C-롱 (돌파·재지지)", "2순위 눌림 롱 별도 검증",
                      "A/B 숏은 추세 전환 확인 후"]
    else:
        trend = "mixed"
        priorities = ["1순위 관망 (15분·1시간 방향 충돌)",
                      "A/B-숏 또는 C-롱은 추세 합의 뒤 재평가"]
    return dict(status="ready", trend=trend, priorities=priorities,
                directions={"5m": d5, "15m": d15, "1h": d1h},
                four_hour_reference=(
                    "down" if q4h[-1] < q4h[-2] else "up"
                    if q4h[-1] > q4h[-2] else "flat")
                if len(q4h) >= 3 else "insufficient_completed_4h")


def _atr(bars, period=14):
    tr = []
    for p, c in zip(bars[:-1], bars[1:]):
        tr.append(max(c["high"]-c["low"], abs(c["high"]-p["close"]),
                      abs(c["low"]-p["close"])))
    return sum(tr[-period:])/period if len(tr) >= period else None


def evaluate_b_failed_reclaim(bars):
    """Separate B-short candidate: break -> failed reclaim -> known T -> RR.

    No guessed target, incomplete candle, or same-candle confirmation.
    Confirmation must occur within 1-3 completed bars, close near old support,
    and not reclaim the level on ANY intermediate completed bar.
    """
    direction = trend_priority(bars)
    out = dict(policy=POLICY, trend=direction["trend"],
               priorities=direction["priorities"], status="not_evaluated")
    if direction["status"] != "ready":
        return dict(out, status=direction["status"])
    if direction["trend"] != "bearish":
        return dict(out, status="not_bearish_priority")
    if len(bars) < MIN_HISTORY or not _valid(bars[-min(240, len(bars)):]):
        return dict(out, status="invalid_source")
    i = len(bars)-1
    for j in range(i-1, max(LOOKBACK_BREAK, i-MAX_CONFIRM_BARS)-1, -1):
        baseline = bars[j-20:j]
        if len(baseline) != 20:
            continue
        support = min(float(x["low"]) for x in bars[j-LOOKBACK_BREAK:j])
        br = bars[j]
        atr = _atr(bars[max(0,j-15):j+1])
        vol = median(float(b["volume"]) for b in baseline)
        if not atr or vol <= 0:
            continue
        if not (br["close"] < support - max(.10*atr, .1)
                and br["close"] < br["open"]
                and br["volume"] >= vol*1.5):
            continue
        if any(b["close"] >= support for b in bars[j+1:i+1]):
            return dict(out, status="support_reclaimed",
                        breakout_at=br["start"], broken_support=support)
        cur = bars[i]
        if (cur["high"] < support-.35*atr or cur["close"] >= support
                or cur["close"] < support-.75*atr):
            return dict(out, status="retest_not_confirmed_or_chasing",
                        breakout_at=br["start"], broken_support=support,
                        distance_from_support=support-cur["close"])
        if cur["close"] >= cur["open"]:
            return dict(out, status="waiting_bearish_rejection",
                        breakout_at=br["start"], broken_support=support)
        stop = rounded(max(br["high"], support, cur["high"])+max(.30*atr, cur["close"]*.00035),
                       .1, up=True)
        observed_at = cur["received_at"]
        try:
            barriers = available_barriers(bars[:i+1], -1, cur["close"], observed_at)
        except (TypeError, ValueError, KeyError, OverflowError):
            return dict(out, status="target_source_invalid")
        if not barriers:
            return dict(out, status="no_known_target",
                        breakout_at=br["start"], broken_support=support,
                        provisional_stop=stop)
        barrier = barriers[0]
        target = rounded(barrier["price"]+max(.1,cur["close"]*.00015), .1, up=True)
        if target >= cur["close"]:
            return dict(out, status="target_not_below_price")
        costs = Costs(min_rr=MIN_RR)
        plan = dict(lane="b_failed_reclaim", direction=-1, stop=stop,
                    target=target, trigger=support, signal_start=cur["start"])
        verdict, ratio = judge_entry(plan, cur["close"], costs)
        limit = entry_bound(-1, stop, target, costs.pnl, MIN_RR)
        out.update(breakout_at=br["start"], confirmation_at=cur["start"],
                   broken_support=support, close=cur["close"], stop=stop,
                   target=target, structural_target_available_at=barrier["available_at"],
                   minimum_permissible_short_entry=limit,
                   net_rr_at_close=ratio, volume_multiple=br["volume"]/vol)
        return dict(out, status="qualified_shadow" if verdict=="qualified" else verdict)
    return dict(out, status="no_recent_qualified_breakout")


def persist_b_retest_shadow(ledger, bars, *, now, started):
    """Immutable research audit for every fresh 5m decision. No send/order."""
    if not bars:
        return {"status":"no_data"}
    z = bars[-1]
    if z["start"]+300 <= started or not z["start"]+300 <= now <= z["start"]+330:
        return {"status":"stale_or_before_start"}
    result = evaluate_b_failed_reclaim(bars)
    fingerprint = sha256(json.dumps(
        [{k:r[k] for k in ("start","open","high","low","close","volume","quality")}
         for r in bars[-min(96,len(bars)):]], sort_keys=True,
         separators=(",",":"),allow_nan=False).encode()).hexdigest()
    payload = dict(result, raw_source="lbank_1m_stream_derived_5m",
                   source_limit="stream_inferred_not_independent_rest",
                   input_sha256=fingerprint, observed_at=now,
                   delivery="shadow_only_no_telegram_or_order")
    with ledger.db:
        ledger.db.execute("""CREATE TABLE IF NOT EXISTS video_b_retest_shadow(
            bar_start INTEGER PRIMARY KEY, evaluated_at REAL NOT NULL,
            status TEXT NOT NULL, evidence TEXT NOT NULL)""")
        ledger.db.execute("""INSERT OR IGNORE INTO video_b_retest_shadow VALUES(?,?,?,?)""",
                          (z["start"], now, result["status"],
                           json.dumps(payload,sort_keys=True,ensure_ascii=False,allow_nan=False)))
    return result


def replay_b_retest(bars, *, horizon=12, receipt_basis="provided_receipts"):
    """Causal offline probe partitioned into contiguous, verified runs.

    A target/stop touched together in one candle is unresolved. An old archive
    with nominal receipts is NEVER treated as independently verified live data.
    """
    statuses = Counter()
    outcomes = Counter()
    examples = []
    run_start = 0
    for i in range(len(bars)):
        if i and bars[i]["start"] - bars[i-1]["start"] != 300:
            run_start = i
        if i-run_start+1 < MIN_HISTORY:
            continue
        window = bars[max(run_start,i-239):i+1]
        res = evaluate_b_failed_reclaim(window)
        statuses[res["status"]] += 1
        if res["status"] != "qualified_shadow":
            continue
        if i+1 >= len(bars) or bars[i+1]["start"] != bars[i]["start"]+300:
            outcomes["no_followup"] += 1
            continue
        entry = bars[i+1]["open"]
        stop, target = res["stop"], res["target"]
        costs = Costs(min_rr=MIN_RR)
        verdict, _ = judge_entry(dict(lane="b_failed_reclaim",direction=-1,
                                      stop=stop,target=target),entry,costs)
        if verdict != "qualified":
            outcomes["next_open_"+verdict] += 1
            continue
        status = "unresolved"
        for k in range(i+1,min(len(bars),i+1+horizon)):
            b = bars[k]
            if b["start"] != bars[i]["start"]+(k-i)*300:
                status = "gap_unresolved"
                break
            if b["open"] >= stop:status="stop";break
            if b["open"] <= target:status="target";break
            hit_s=b["high"]>=stop
            hit_t=b["low"]<=target
            if hit_s and hit_t:status="same_bar_unknown";break
            if hit_s:status="stop";break
            if hit_t:status="target";break
        outcomes[status]+=1
        examples.append({"at":res["confirmation_at"],"entry":entry,
                         "stop":stop,"target":target,"outcome":status})
    return dict(policy=POLICY,production_ready=False,
                sample_bars=len(bars),receipt_basis=receipt_basis,
                status_counts=dict(statuses),outcomes=dict(outcomes),
                example_entries=examples[:20])
