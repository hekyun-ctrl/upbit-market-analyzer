"""Opt-in LBank 1m momentum TEST entry candidates, never executable orders.

This is an intentionally separate experimental notification lane. Historical
proxy viability is unproven, and the target is a 2.05R VOLATILITY PROJECTION,
not a confirmed support. Cannot be represented as a verified formal signal.
Native exchange-derived 1m receipts are mandatory. No account API or orders.
"""
from dataclasses import asdict
from hashlib import sha256
import json
import logging
import math
import sqlite3
from statistics import median

from lbank_b_early_1m_shadow import _valid_minutes
from lbank_b_retest_research import _valid, trend_priority, MIN_HISTORY, _atr
from lbank_structural_target_plan import available_barriers, rounded
from lbank_b_target_projection import project_short_target
from research.zerobase.video_entry import Costs, judge_entry

POLICY = "b_native_first_microbreak_test_v1"
LOG = logging.getLogger("lbank_b_momentum_trial")
COOLDOWN = 1800
MAX_CLOSE_AGE = 24
MIN_VOL_MULTIPLE = 1.5
ATR_MULTIPLE_CAP = 8.0
MAX_CANDLE_BODY_ATR = 1.8
MODEL_TARGET_RR = 2.05
TEST_RR = Costs(min_rr=2.0)
RETENTION = 7 * 86400


def _atr_minute(rows):
    if len(rows) < 16:
        return None
    trs = [
        max(b["high"]-b["low"],
            abs(b["high"]-a["close"]), abs(b["low"]-a["close"]))
        for a,b in zip(rows[-15:-1], rows[-14:])]
    return sum(trs)/len(trs)


def _prior_five(minute, fives):
    # A 5m candle overlapping the signal minute MUST NOT be historical input.
    return [b for b in fives if b["start"]+300<=minute["start"] and
            b.get("received_at",float("inf"))<=minute["received_at"]]


def evaluate_trial(minutes, fives):
    """Return a test-only plan at the last completed minute; no live quote."""
    out={"policy":POLICY,"status":"insufficient_history","kind":"test_only"}
    if len(minutes)<61 or not _valid_minutes(minutes[-61:]):
        return out
    curr=minutes[-1]
    fives=_prior_five(curr,fives)
    if len(fives)<MIN_HISTORY or not _valid(fives[-min(240,len(fives)):]):
        return dict(out,status="insufficient_or_invalid_five_history")
    if not fives[-1]["start"]+300<=curr["start"]<fives[-1]["start"]+600:
        return dict(out,status="five_clock_mismatch")
    regime=trend_priority(fives)
    out.update(trend=regime["trend"],trend_priority=regime["priorities"][0])
    if regime["trend"]!="bearish":
        return dict(out,status="not_bearish")
    atr=_atr_minute(minutes)
    if atr is None or atr<=0 or not math.isfinite(atr):
        return dict(out,status="invalid_atr")
    base=median(x["volume"] for x in minutes[-21:-1])
    if base<=0:
        return dict(out,status="missing_volume_baseline")
    multiple=curr["volume"]/base
    threshold=min(x["low"] for x in minutes[-7:-1])-.05*atr
    price=curr["close"]
    out.update(signal_start=curr["start"],signal_close=price,atr1=atr,
               volume_multiple=round(multiple,3),break_threshold=threshold)
    if not (price<threshold and curr["close"]<curr["open"]):
        return dict(out,status="no_new_lower_break")
    if multiple<MIN_VOL_MULTIPLE:
        return dict(out,status="volume_below_threshold")
    if (curr["open"]-price)/atr>MAX_CANDLE_BODY_ATR:
        return dict(out,status="already_extended")
    stop=rounded(max(x["high"] for x in minutes[-4:])+.2*atr,.1,up=True)
    risk=stop-price
    if risk<=0 or risk/price>TEST_RR.max_stop_fraction:
        return dict(out,status="invalid_or_wide_stop",stop=stop)
    projection=project_short_target(
        price,stop,atr,costs=TEST_RR,
        desired_rr=MODEL_TARGET_RR,max_atr=ATR_MULTIPLE_CAP)
    if projection["status"]!="projected_target_candidate":
        return dict(out,status=projection["status"],stop=stop,
                    projected_target=projection.get("target"),
                    distance_atr=projection.get("distance_atr"))
    target=projection["target"]
    # If a previously confirmed lower 5m support lies BEFORE the projection,
    # the signal is blocked rather than knowingly targeting through it.
    try:
        obstacles=available_barriers(fives[-240:],-1,price,curr["received_at"])
    except (KeyError,TypeError,ValueError,OverflowError):
        return dict(out,status="invalid_target_evidence")
    if obstacles and obstacles[0]["price"]>target:
        return dict(out,status="known_support_before_projection",
                    stop=stop,estimated_target=target,
                    prior_known_support=obstacles[0]["price"])
    plan=dict(lane="b_native_first_microbreak_test",direction=-1,
              stop=stop,target=target)
    verdict,rr=judge_entry(plan,price,TEST_RR)
    if verdict!="qualified":
        return dict(out,status=verdict,stop=stop,target=target,net_rr=rr)
    return dict(out,status="test_candidate",direction="short",stop=stop,
                target=target,net_rr=rr,
                target_basis="bounded_volatility_projection_NOT_support",
                stop_basis="known_4x_1m_high_plus_20pct_atr1",
                costs_assumptions=asdict(TEST_RR))


def evaluate_five_break(fives):
    """Independent 5m first-break short, entirely separate from RSI.

    Only prior completed 5m bars set support. Projected exits are test-only.
    """
    out={"policy":POLICY+"_five","status":"insufficient_five_history",
         "kind":"test_only"}
    if len(fives)<MIN_HISTORY or not _valid(fives[-min(240,len(fives)):]):
        return out
    rank=trend_priority(fives)
    out.update(trend=rank["trend"],trend_priority=rank["priorities"][0])
    if rank["trend"]!="bearish":
        return dict(out,status="not_bearish")
    cur=fives[-1]
    atr=_atr(fives[-16:])
    base=median(x["volume"] for x in fives[-21:-1])
    if not atr or atr<=0 or not base or base<=0:
        return dict(out,status="invalid_atr_or_volume")
    price=float(cur["close"])
    support=min(x["low"] for x in fives[-7:-1])
    buffer=max(.10*atr,price*.00010)
    multiple=cur["volume"]/base
    out.update(signal_start=cur["start"],bar_seconds=300,
               signal_close=price,break_threshold=support-buffer,
               atr1=atr,volume_multiple=round(multiple,3),
               broken_support=support)
    if not (price<support-buffer and price<cur["open"]):
        return dict(out,status="no_five_support_break")
    if multiple<MIN_VOL_MULTIPLE:
        return dict(out,status="volume_below_threshold")
    if (cur["open"]-price)/atr>MAX_CANDLE_BODY_ATR:
        return dict(out,status="already_extended")
    stop=rounded(max(support,max(b["high"] for b in fives[-3:]))
                 +.2*atr,.1,up=True)
    if stop<=price or (stop-price)/price>TEST_RR.max_stop_fraction:
        return dict(out,status="invalid_or_wide_stop",stop=stop)
    projection=project_short_target(
        price,stop,atr,costs=TEST_RR,
        desired_rr=MODEL_TARGET_RR,max_atr=ATR_MULTIPLE_CAP)
    if projection["status"]!="projected_target_candidate":
        return dict(out,status=projection["status"],stop=stop,
                    projected_target=projection.get("target"),
                    distance_atr=projection.get("distance_atr"))
    target=projection["target"]
    try:
        barriers=available_barriers(fives[-240:],-1,price,cur["received_at"])
    except (ValueError,KeyError,TypeError,OverflowError):
        return dict(out,status="invalid_target_evidence")
    if barriers and barriers[0]["price"]>target:
        return dict(out,status="known_support_before_projection",
                    stop=stop,estimated_target=target,
                    prior_known_support=barriers[0]["price"])
    verdict,rr=judge_entry(dict(lane="b_native_first_5m_break_test",
                       direction=-1,stop=stop,target=target),price,TEST_RR)
    if verdict!="qualified":
        return dict(out,status=verdict,stop=stop,target=target,net_rr=rr)
    return dict(out,status="test_candidate",direction="short",
                stop=stop,target=target,net_rr=rr,
                target_basis=projection["target_basis"],
                stop_basis="causal_3x_5m_high_plus_20pct_ATR",
                costs_assumptions=asdict(TEST_RR))


def quote_check(plan, *, now, get_book, clock):
    """Fail-closed live sell bid quote. Do not use historical signal close."""
    if plan.get("status")!="test_candidate":
        return dict(status="not_a_candidate")
    start=plan["signal_start"]
    seconds=plan.get("bar_seconds",60)
    if seconds not in (60,300):
        return dict(status="invalid_bar_period")
    if not start+seconds<=now<=start+seconds+MAX_CLOSE_AGE:
        return dict(status="stale_quote_window")
    try:
        bid,ask=get_book()
    except Exception as exc:
        return dict(status="book_unavailable",error_type=type(exc).__name__)
    observed_at=clock()
    if not now<=observed_at<=min(now+5,start+seconds+MAX_CLOSE_AGE):
        return dict(status="book_too_slow")
    if (not all(type(x) in (float,int) and math.isfinite(x)
                for x in (bid,ask)) or not 0<bid<ask):
        return dict(status="invalid_book")
    spread=(ask-bid)/((ask+bid)/2)
    if spread>.0005:
        return dict(status="spread_too_wide",spread=spread)
    # Prevent late chase after even a one-minute deep market move. The
    # original target and stop stay frozen; quote is independently re-checked.
    if abs(bid-plan["signal_close"])>min(plan["atr1"]*.5,
                                       plan["signal_close"]*.001):
        return dict(status="quote_moved_since_signal",bid=bid)
    from research.zerobase.video_entry import judge_entry
    check,rr=judge_entry(dict(lane="b_native_first_microbreak_test",
                               direction=-1,stop=plan["stop"],target=plan["target"]),
                         bid,TEST_RR)
    if check!="qualified":
        return dict(status=check,net_rr=rr,bid=bid)
    return dict(status="quote_qualified_test",bid=bid,ask=ask,spread=spread,
                net_rr=rr,observed_at=observed_at)


def _message(plan,book):
    from datetime import datetime,timedelta,timezone
    kst=timezone(timedelta(hours=9))
    stamp=datetime.fromtimestamp(plan["signal_start"]+plan.get("bar_seconds",60),kst).strftime("%m-%d %H:%M KST")
    candle_label="5분" if plan.get("bar_seconds",60)==300 else "1분"
    return (
        "[ZB BTCUSDT · 실험 진입 후보 / 실거래 검증 전]\n"
        "⚠️ 정식 수익성 검증을 통과한 알림이 아닙니다. 자동 주문 없음\n"
        f"방향: 숏(매도) | {candle_label}봉 종료: {stamp}\n"
        f"실시간 매도 호가(주문가 보장 안 됨): {book['bid']:,.1f} USDT\n"
        f"구조 손절 후보: {plan['stop']:,.1f} USDT\n"
        f"변동성 기반 목표 *가설*: {plan['target']:,.1f} USDT\n"
        f"가정 비용 후 손익비: {book['net_rr']:.2f}:1 "
        "(목표 도달 확률 검증되지 않음)\n"
        f"첫 {candle_label}봉 지지 이탈: {plan['break_threshold']:,.1f}, "
        f"거래량 {plan['volume_multiple']:.2f}배\n"
        "목표는 확인된 하단 지지선이 아닙니다. 모의 테스트 목적으로만 판단하세요. "
        "50배 레버리지에서는 변동·슬리피지에 특히 주의하세요."
    )


def deliver_trial(ledger, plan, *, now, get_book, clock, send, enabled,
                  started):
    """Durably dedupe and cooldown; only user-requested experimental texts."""
    if plan.get("status")!="test_candidate":
        return plan.get("status","not_a_candidate")
    bar=plan["signal_start"]
    if bar+plan.get("bar_seconds",60)<=started:return "before_worker_start"
    if not enabled or send is None:return "test_delivery_disabled"
    book=quote_check(plan,now=now,get_book=get_book,clock=clock)
    if book["status"]!="quote_qualified_test":
        return book["status"]
    with ledger.db:
        ledger.db.execute("""CREATE TABLE IF NOT EXISTS
            video_b_trial_delivery(minute_start INTEGER PRIMARY KEY,
            status TEXT NOT NULL,sent_at REAL NOT NULL,evidence TEXT NOT NULL)""")
        cutoff=bar-RETENTION
        ledger.db.execute("DELETE FROM video_b_trial_delivery WHERE minute_start<?",(cutoff,))
        recent=ledger.db.execute("""SELECT minute_start FROM video_b_trial_delivery
             WHERE minute_start>? ORDER BY minute_start DESC LIMIT 1""",
             (bar-COOLDOWN,)).fetchone()
        if recent:return "cooldown"
        info=json.dumps(dict(plan=plan,quote=book),
                        sort_keys=True,allow_nan=False)
        won=ledger.db.execute("""INSERT OR IGNORE INTO video_b_trial_delivery
                        VALUES(?,?,?,?)""",(bar,"reserved",now,info)).rowcount
    if not won:return "already_reserved"
    result="unconfirmed_no_retry"
    try:
        if send(_message(plan,book)):
            result="telegram_accepted_test"
    except Exception as exc:
        LOG.warning("VIDEO_B_TRIAL_SEND_ERROR type=%s",type(exc).__name__)
    finally:
        with ledger.db:
            ledger.db.execute("""UPDATE video_b_trial_delivery
                SET status=? WHERE minute_start=?""",(result,bar))
    return result

