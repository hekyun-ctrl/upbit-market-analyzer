"""User-authorized BTCUSDT fixed-level Telegram observation notices.

The watcher is INDEPENDENT of RSI and formal trade-entry approval.
Uses only two consecutive completed LBank-stream bars; never an
incomplete bar, live quote, broker order, or a post-restart replay.
"""
from datetime import datetime, timedelta, timezone
import logging
import math
import sqlite3

LOG = logging.getLogger("lbank_fixed_level_alerts")
KST = timezone(timedelta(hours=9))
LOWER_SUPPORT = 62525.0
UPPER_RECOVERY = 63060.0
MAX_CLOSE_AGE = 30
RETENTION_SECONDS = 7 * 86400
COOLDOWN_SECONDS = {60: 15 * 60, 300: 15 * 60}


def _valid_pair(bars, period, now):
    if period not in (60, 300) or len(bars) < 2:
        return False
    prev, last = bars[-2:]
    try:
        if (type(prev["start"]) is not int or type(last["start"]) is not int
                or prev["start"] % period or last["start"] - prev["start"] != period):
            return False
        for b in (prev, last):
            if b.get("quality") != "stream_closed" or b.get("period") != period:
                return False
            o, h, l, c, v = [float(b[k]) for k in ("open", "high", "low", "close", "volume")]
            received = b["received_at"]
            if (not all(math.isfinite(x) for x in (o, h, l, c, v))
                    or l <= 0 or v < 0 or not l <= min(o, c) <= max(o, c) <= h
                    or type(received) not in (int, float) or not math.isfinite(received)
                    or not b["start"] + period <= received <= b["start"] + period + MAX_CLOSE_AGE
                    or received > now):
                return False
    except (KeyError, TypeError, ValueError, OverflowError):
        return False
    return True


def evaluate_level_cross(bars, period, now):
    """Only a new crossover between successive closed candle prices."""
    if len(bars) < 2:
        return {"status": "insufficient_history"}
    if not _valid_pair(bars, period, now):
        return {"status": "invalid_source"}
    previous, current = bars[-2:]
    before, after = float(previous["close"]), float(current["close"])
    if before >= LOWER_SUPPORT and after < LOWER_SUPPORT:
        direction, level = "down", LOWER_SUPPORT
    elif before <= UPPER_RECOVERY and after > UPPER_RECOVERY:
        direction, level = "up", UPPER_RECOVERY
    else:
        return {"status": "no_fresh_cross"}
    return {"status": "candidate", "period": period, "direction": direction,
            "level": level, "start": current["start"], "close": after,
            "previous_close": before, "volume": float(current["volume"])}


def format_level_alert(event):
    period = event["period"]
    side = event["direction"]
    end = event["start"] + period
    at = datetime.fromtimestamp(end, KST).strftime("%m-%d %H:%M KST")
    phase = "1분봉 조기 경보" if period == 60 else "5분봉 마감 확인"
    meaning = "62,525 하향 이탈" if side == "down" else "63,060 상향 회복"
    next_step = ("5분봉 마감까지 지지 이탈 유지 여부 확인" if period == 60 else
                 "후속 봉의 재회복·재이탈과 실제 손익비 검증 필요")
    return (
        f"[ZB LBank BTCUSDT · 지정 가격선 {phase}]\n"
        "⚠️ 가격 변화 관찰 전용 · 진입 신호 아님 · 자동 주문 없음\n"
        f"감시 사건: {meaning}\n"
        f"확인 시각: {at} (스트림 추정 완료봉)\n"
        f"직전 {period // 60}분봉 종가: {event['previous_close']:,.1f} USDT\n"
        f"이번 {period // 60}분봉 종가: {event['close']:,.1f} USDT\n"
        f"감시 기준선: {event['level']:,.1f} USDT\n"
        f"다음 확인: {next_step}\n"
        "실시간 호가·체결가 아님. LBank 1분봉 스트림 기반이며 독립 REST 검증 전.\n"
        "손절·목표·수수료 검증 전이므로 50배 선물 진입 권유가 아닙니다."
    )


def send_level_alert(ledger, bars, period, *, now, started, send, enabled):
    """Persistent reserve-before-send dedupe; never backfill old crossings."""
    if not enabled or send is None:
        return "disabled"
    if len(bars) < 2:
        return "insufficient_history"
    last = bars[-1]
    start = last.get("start")
    if type(start) is not int:
        return "invalid_source"
    completed = start + period
    if completed <= started:
        return "before_worker_start"
    if not completed <= now <= completed + MAX_CLOSE_AGE:
        return "stale_or_future"
    event = evaluate_level_cross(bars, period, now)
    if event["status"] != "candidate":
        return event["status"]
    side, level = event["direction"], event["level"]
    try:
        with ledger.db:
            ledger.db.execute("""CREATE TABLE IF NOT EXISTS video_fixed_level_notice (
                period INTEGER NOT NULL,
                level REAL NOT NULL,
                direction TEXT NOT NULL,
                bar_start INTEGER NOT NULL,
                reserved_at REAL NOT NULL,
                status TEXT NOT NULL,
                PRIMARY KEY(period, level, direction, bar_start))""")
            ledger.db.execute("DELETE FROM video_fixed_level_notice WHERE bar_start<?",
                              (start - RETENTION_SECONDS,))
            earlier = ledger.db.execute(
                """SELECT 1 FROM video_fixed_level_notice
                   WHERE period=? AND level=? AND direction=?
                     AND bar_start>? LIMIT 1""",
                (period, level, side, start - COOLDOWN_SECONDS[period])
            ).fetchone()
            if earlier:
                return "cooldown"
            inserted = ledger.db.execute(
                """INSERT OR IGNORE INTO video_fixed_level_notice
                   VALUES(?,?,?,?,?,'reserved')""",
                (period, level, side, start, now)
            ).rowcount
            if not inserted:
                return "already_reserved"
    except sqlite3.Error as exc:
        LOG.warning("VIDEO_FIXED_LEVEL_LEDGER_FAILURE type=%s", type(exc).__name__)
        return "reservation_failed"
    accepted = False
    try:
        accepted = bool(send(format_level_alert(event)))
    except Exception as exc:
        LOG.warning("VIDEO_FIXED_LEVEL_SEND_FAILURE type=%s", type(exc).__name__)
    status = "accepted" if accepted else "unconfirmed"
    try:
        with ledger.db:
            ledger.db.execute(
                """UPDATE video_fixed_level_notice SET status=?
                   WHERE period=? AND level=? AND direction=? AND bar_start=?""",
                (status, period, level, side, start))
    except sqlite3.Error as exc:
        LOG.warning("VIDEO_FIXED_LEVEL_FINISH_FAILURE type=%s", type(exc).__name__)
    LOG.info("VIDEO_FIXED_LEVEL_NOTICE period=%d side=%s level=%.1f bar=%d status=%s",
             period, side, level, start, status)
    return f"{side}:{status}"
