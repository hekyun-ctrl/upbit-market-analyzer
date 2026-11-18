"""LBank BTCUSDT every-five-completed-5m-candle Telegram status.

Scheduled on 25-minute boundaries in Korea time; each run checks five
verified consecutive closed candles. The bot, not a ChatGPT human,
generates deterministic status reports. No orders or approved trade calls.
A missing/stale feed still produces a transparent NO-ENTRY report.
"""
from datetime import datetime, timedelta, timezone
import logging
import math
import sqlite3
import time

from lbank_loss_review_guard import parallel_observation, entry_delivery_allowed
from lbank_b_retest_research import trend_priority
from lbank_opportunity_review import evaluate_review

LOG = logging.getLogger("lbank_five_bar_checkin")
KST = timezone(timedelta(hours=9))
KST_SECONDS = 9 * 3600
PERIOD_SECONDS = 5 * 300
MAX_NOTICE_DELAY = 90
WAIT_FOR_SOURCE_SECONDS = 40
FIRST_CHECK_DELAY = 10
LEDGER_RETENTION = 7 * 86400
STATUS_NAMES = {
    "book_unavailable": "실시간 호가 조회 실패",
    "stale_or_wide_book": "실시간 호가 지연 또는 스프레드 과다",
    "no_known_target": "확인된 구조 목표 부재",
    "no_high_priority_structure": "A/B/C 신호 미충족",
    "higher_timeframe_source_invalid": "상위봉 자료 무결성 오류",
    "screen_rejected": "손익비·호가·목표가 검증 미통과",
    "insufficient_structure_history": "A/B/C 평가에 필요한 연속 21봉 부족",
}


def _slot_end(now):
    """The last 25-minute local-time boundary (without hourly reset)."""
    return int((now + KST_SECONDS) // PERIOD_SECONDS) * PERIOD_SECONDS - KST_SECONDS


def _validate_five(bars, end, now):
    """Require exactly the latest five fresh, continuous *verified* closes."""
    if len(bars) < 5:
        return False
    rows = bars[-5:]
    first = end - PERIOD_SECONDS
    for i, row in enumerate(rows):
        try:
            start = row["start"]
            received = row["received_at"]
            o, h, l, c, v = [float(row[k]) for k in
                             ("open", "high", "low", "close", "volume")]
            if (type(start) is not int or start != first + i*300 or
                    row["quality"] != "stream_closed" or
                    row.get("period", 300) != 300 or
                    type(received) not in (int, float) or
                    not start + 300 <= received <= start + 330 or
                    received > now or
                    not all(math.isfinite(z) for z in (o,h,l,c,v)) or
                    not 0 < l <= min(o,c) <= max(o,c) <= h or v < 0):
                return False
        except (TypeError, ValueError, KeyError, OverflowError):
            return False
    return True


def _observe(bars, *, now, get_book, clock):
    """Only completed source bars; never invent a target or trade approval."""
    rows = bars[-5:]
    first, last = rows[0], rows[-1]
    opening, close = float(first["open"]), float(last["close"])
    change_pct = (close/opening - 1)*100
    move = "하락" if change_pct < -0.10 else "상승" if change_pct > 0.10 else "횡보"
    result = {
        "source": "verified", "status": "no_entry", "decision": "진입 보류",
        "price": close, "opening": opening, "move": move,
        "change_pct": change_pct,
        "range_high": max(float(r["high"]) for r in rows),
        "range_low": min(float(r["low"]) for r in rows),
        "bars": len(bars), "trend": "상위봉 미확인",
        "priority": "A/B/C 진입 후보 없음", "reason": "21봉 미만 · 조기 구조 평가 불가",
        "review": None,
    }
    if len(bars) < 21:
        return result
    obs = parallel_observation(bars)
    if obs["status"] != "observed":
        result["reason"] = "구조 평가 자료 오류: " + obs["status"]
        return result
    if len(bars) >= 72:
        rank = trend_priority(bars)
        if rank.get("status") == "ready":
            result["trend"] = {
                "bearish": "15분·1시간 하락 우세", "bullish": "15분·1시간 상승 우세",
                "mixed": "15분·1시간 방향 충돌",
            }.get(rank.get("trend"), "상위봉 미확인")
    top = [label for label in ("B","A","C")
           if obs["lanes"][label]["status"] == "observed_not_entry"]
    if top:
        result["priority"] = f"{top[0]}형 {'롱' if top[0]=='C' else '숏'} 후보 관찰"
    else:
        # A directional change is not evidence of a qualified entry.
        result["priority"] = ("숏 방향 참고 · 구조 미충족" if move=="하락" else
                              "롱 방향 참고 · 구조 미충족" if move=="상승" else
                              "양방향 대기 · 구조 미충족")
    result["reason"] = "A/B/C 구조 및 호가·목표 검증 미통과"
    review = evaluate_review(bars, now=now, get_book=get_book, clock=clock)
    if review.get("status") == "review_candidate":
        result["status"] = "candidate"
        result["decision"] = "구조 후보 조건 충족 (실전 진입 미승인)"
        result["review"] = review
        result["priority"] = f"{review['lane']}형 {'롱' if review['direction']==1 else '숏'} 최우선 관찰"
        result["reason"] = "모형의 호가·비용 손익비 통과 · 실거래 성과 검증 미완료"
    else:
        reason = STATUS_NAMES.get(review.get("status"), review.get("status"))
        result["reason"] = str(reason)
        if review.get("status") == "screen_rejected":
            codes = review.get("reasons") or []
            result["reason"] += " (" + ", ".join(str(code) for code in codes[:3]) + ")"
    return result


def format_checkin(slot, data):
    at = datetime.fromtimestamp(slot, KST).strftime("%m-%d %H:%M KST")
    title = f"[BTCUSDT | 5분봉 5개 정기 점검 | {at}]"
    if data["source"] != "verified":
        return (
            title + "\n"
            "결론: 진입 금지 · 최신 5분봉 5개를 검증하지 못함\n"
            "롱/숏: 판단 보류\n"
            "진입가·손절가·목표가: 제시 불가 (시세 자료 부족)\n"
            "수집 지연·재연결·완료봉 누락 여부를 먼저 확인해야 합니다.\n"
            "※ 실전 진입 승인이 아니며 자동주문 없음"
        )
    message = (
        title + "\n"
        f"결론: {data['decision']}\n"
        f"관찰 우선순위: {data['priority']}\n"
        f"5봉 시가→종가 {data['opening']:,.1f} → {data['price']:,.1f} USDT"
        f" ({data['change_pct']:+.2f}%, {data['move']})\n"
        f"5봉 고점 {data['range_high']:,.1f} / 저점 {data['range_low']:,.1f}\n"
        f"상위 추세: {data['trend']} / 유효 연속 5분봉 {data['bars']}개\n"
    )
    if data["review"] is not None:
        e = data["review"]
        message += (
            f"체크 시점 호가: {e['quote']:,.1f}\n"
            f"구조 손절 가설: {e['stop']:,.1f} / 확인된 목표 가설: {e['target']:,.1f}\n"
            f"비용 포함 가정 손익비: {e['net_rr_assumed']:.2f}:1\n"
            "위 가격은 체결 보장 없는 연구 가설이며 실전 진입 승인이 아닙니다.\n"
        )
    else:
        message += "진입가·손절가·목표가: 검증 미통과로 제시하지 않음\n"
    if data.get("recent_fast_watch"):
        message += f"최근 25분 조기 가격 움직임: {data['recent_fast_watch']} · 진입 승인은 별도\n"
    message += (
        f"주요 사유: {data['reason']}\n"
        "※ 실전 신호 검증 잠금 유지 · 자동주문 없음 · 레버리지 위험 주의"
    )
    return message


def send_five_bar_checkin(ledger, bars, *, now, started, get_book,
                          send, enabled, clock=time.time):
    """One message per closed five-bar block; persistent no-duplicate reservation.

    Missing data is reported as an explicit NO-ENTRY only after a short
    lateness grace period. A candle arriving after the grace is not backfilled.
    """
    if not enabled or send is None:
        return "disabled"
    slot = _slot_end(now)
    lag = now-slot
    if slot <= started:
        return "before_start"
    if lag < FIRST_CHECK_DELAY or lag > MAX_NOTICE_DELAY:
        return "not_due"
    valid = _validate_five(bars, slot, now)
    if not valid and lag < WAIT_FOR_SOURCE_SECONDS:
        return "waiting_for_fresh_bar"
    data = (_observe(bars, now=now, get_book=get_book, clock=clock)
            if valid else {"source": "unavailable"})
    if valid:
        try:
            last_watch = ledger.db.execute(
                """SELECT direction,period FROM video_price_momentum_notice
                WHERE bar_start>=? AND bar_start<? AND status='accepted'
                ORDER BY bar_start DESC LIMIT 1""",
                (slot-25*60,slot)).fetchone()
            if last_watch:
                data["recent_fast_watch"] = (
                    ("상승" if last_watch[0]=="up" else "하락")
                    +f"({last_watch[1]//60}분봉)")
        except sqlite3.OperationalError:
            pass
    try:
        with ledger.db:
            ledger.db.execute("""CREATE TABLE IF NOT EXISTS video_five_bar_checkins(
                slot_end INTEGER PRIMARY KEY, recorded_at REAL NOT NULL,
                status TEXT NOT NULL)""")
            ledger.db.execute(
                "DELETE FROM video_five_bar_checkins WHERE slot_end<?",
                (slot-LEDGER_RETENTION,))
            if not ledger.db.execute(
                "INSERT OR IGNORE INTO video_five_bar_checkins VALUES(?,?,'reserved')",
                (slot, now)).rowcount:
                return "already_reserved"
    except sqlite3.Error:
        LOG.warning("FIVE_BAR_CHECKIN_LEDGER_UNAVAILABLE")
        return "ledger_unavailable"
    accepted = False
    try:
        accepted = bool(send(format_checkin(slot, data)))
    except Exception as exc:
        LOG.warning("FIVE_BAR_CHECKIN_SEND_FAILURE type=%s", type(exc).__name__)
    status = "accepted" if accepted else "unconfirmed_no_retry"
    try:
        with ledger.db:
            ledger.db.execute(
                "UPDATE video_five_bar_checkins SET status=? WHERE slot_end=?",
                (status, slot))
    except sqlite3.Error:
        LOG.warning("FIVE_BAR_CHECKIN_FINISH_FAILURE")
    return (data.get("status", "source_unavailable") + ":" + status)
