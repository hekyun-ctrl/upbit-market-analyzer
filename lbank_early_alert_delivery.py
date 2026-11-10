"""Opt-in Telegram notifications for observation-only BTCUSDT 5m events.

Never places orders, edits the live RSI/CCI strategy, or labels an early
breakout as an actionable entry. All reservations are persisted before send.
"""
from datetime import datetime, timedelta, timezone
import logging
import sqlite3

from lbank_early_breakout_audit import evaluate_early_breakout

LOG = logging.getLogger("lbank_early_alert_delivery")
KST = timezone(timedelta(hours=9))
COOLDOWN = 30 * 60
MAX_AGE = 30
FEED_STALE_SECONDS = 15 * 60
FEED_WARNING_COOLDOWN = 6 * 3600


def _setup_tables(db):
    db.execute("""CREATE TABLE IF NOT EXISTS video_early_alert_delivery(
        bar_start INTEGER PRIMARY KEY,
        direction TEXT NOT NULL,
        reserved_at REAL NOT NULL,
        status TEXT NOT NULL
    )""")
    db.execute("""CREATE TABLE IF NOT EXISTS video_system_notices(
        notice_key TEXT PRIMARY KEY,
        at REAL NOT NULL,
        status TEXT NOT NULL
    )""")


def _attempt(send, message):
    try:
        return "accepted" if send(message) else "unconfirmed"
    except Exception as exc:
        LOG.warning("VIDEO_NOTICE_SEND_ERROR type=%s", type(exc).__name__)
        return "unconfirmed"


def format_candidate(observed):
    close_time = datetime.fromtimestamp(observed["start"] + 300, KST).strftime("%m-%d %H:%M KST")
    is_long = observed["direction"] == "long"
    boundary = observed["previous_high"] if is_long else observed["previous_low"]
    return (
        "[엘뱅크 BTCUSDT 5분봉 · 조기 관찰]\n"
        "⚠️ 연구용 시장 움직임 알림 — 매매 진입 신호 아님\n"
        f"관찰 방향: {'상향 돌파' if is_long else '하향 이탈'}\n"
        f"완료봉 시각: {close_time}\n"
        f"완료봉 종가: {observed['close']:,.1f} USDT\n"
        f"직전 6봉 {'고점' if is_long else '저점'}: {boundary:,.1f} USDT\n"
        f"거래량: 직전 20봉 중앙값 대비 {observed['volume_multiple']:.2f}배\n"
        "이 가격은 실시간 호가·진입가가 아닙니다.\n"
        "확정봉은 1분 스트림 합산 추정이며 독립 거래소 REST 검증이 없습니다.\n"
        "손익비·손절·목표·체결 검증 전이며 50배 선물 진입을 권유하지 않습니다."
    )


def send_early_candidate(ledger, bars, *, now, final_status, send, enabled):
    """Send one cooldown-limited observation, only from a fresh closed candle.

    This is independent of full entry recommendations. A valid full Telegram
    entry alert wins; never send a duplicate lower-confidence observation.
    """
    if not enabled or send is None:
        return "disabled"
    if not bars or len(bars) < 21:
        return "insufficient_history"
    bar = bars[-1]
    close_at = bar["start"] + 300
    receipt = bar.get("received_at")
    if (type(receipt) not in (int, float) or
            not close_at <= receipt <= now <= close_at + MAX_AGE):
        return "stale_or_unverified"
    if final_status == "telegram_accepted":
        return "entry_alert_priority"
    candidate = evaluate_early_breakout(bars)
    if candidate["status"] != "candidate":
        return candidate["status"]
    direction, start = candidate["direction"], candidate["start"]
    try:
        with ledger.db:
            _setup_tables(ledger.db)
            prior = ledger.db.execute(
                "SELECT 1 FROM video_early_alert_delivery WHERE direction=? AND bar_start>? LIMIT 1",
                (direction, start - COOLDOWN)).fetchone()
            if prior:
                return "cooldown"
            reservation = ledger.db.execute(
                "INSERT OR IGNORE INTO video_early_alert_delivery VALUES(?,?,?,'reserved')",
                (start, direction, now)).rowcount
            if not reservation:
                return "already_reserved"
    except sqlite3.Error as exc:
        LOG.warning("VIDEO_EARLY_RESERVATION_FAILURE type=%s", type(exc).__name__)
        return "reservation_failed"
    result = _attempt(send, format_candidate(candidate))
    try:
        with ledger.db:
            ledger.db.execute(
                "UPDATE video_early_alert_delivery SET status=? WHERE bar_start=?",
                (result, start))
    except sqlite3.Error as exc:
        LOG.warning("VIDEO_EARLY_FINISH_FAILURE type=%s", type(exc).__name__)
    return result


def activation_check(ledger, *, now, revision, send, enabled):
    """One labeled test per revision, never repeated across worker restarts."""
    if not enabled or send is None:
        return "disabled"
    key = "activation:" + revision
    try:
        with ledger.db:
            _setup_tables(ledger.db)
            if not ledger.db.execute(
                "INSERT OR IGNORE INTO video_system_notices VALUES(?,?,'reserved')",
                (key, now)).rowcount:
                return "already_reserved"
    except sqlite3.Error as exc:
        LOG.warning("VIDEO_ACTIVATION_RESERVATION_FAILURE type=%s", type(exc).__name__)
        return "reservation_failed"
    result = _attempt(send, (
        "[BTCUSDT 5분봉 알림 연결 확인 · 시험 메시지]\n"
        "기존 조건부 진입 알람: 활성\n"
        "조기 돌파 관찰 알림: 활성 (매매 진입 권유 아님)\n"
        "데이터 중단 경고: 활성\n"
        "자동 주문: 비활성\n"
        "이 메시지는 수신 경로 시험이며, 진입 신호나 봉 수신 품질 보증이 아닙니다."
    ))
    try:
        with ledger.db:
            ledger.db.execute(
                "UPDATE video_system_notices SET status=? WHERE notice_key=?",
                (result, key))
    except sqlite3.Error as exc:
        LOG.warning("VIDEO_ACTIVATION_FINISH_FAILURE type=%s", type(exc).__name__)
    return result


def send_feed_warning(ledger, *, now, last_fresh, send, enabled, consecutive=0, required=40):
    """Warn when no fresh *fully analyzable* 5m window for >=15 minutes."""
    if not enabled or send is None:
        return "disabled"
    if now - last_fresh < FEED_STALE_SECONDS:
        return "healthy_or_grace"
    bucket = int(now // FEED_WARNING_COOLDOWN)
    key = "feed_down:" + str(bucket)
    # A once-per-bucket warning; prevents restarts spamming the recipient.
    try:
        with ledger.db:
            _setup_tables(ledger.db)
            if not ledger.db.execute(
                "INSERT OR IGNORE INTO video_system_notices VALUES(?,?,'reserved')",
                (key, now)).rowcount:
                return "already_reserved"
    except sqlite3.Error as exc:
        LOG.warning("VIDEO_FEED_WARNING_RESERVATION_FAILURE type=%s", type(exc).__name__)
        return "reservation_failed"
    result = _attempt(send, (
        "[BTCUSDT 5분봉 감시 주의]\n"
        "15분 이상 기존 진입 분석이 가능한 연속 5분봉 자료가 확보되지 않았습니다.\n"
        f"현재 연속 완료봉: {consecutive}개 / 기존 진입 분석 필요: {required}개\n"
        "새 봉 수신이 있어도 이력이 부족하면 기존 최종 진입 알림은 차단됩니다.\n"
        "관찰 알림 역시 연속 21개 봉이 없으면 발송하지 않습니다.\n"
        "이는 거래 추천이 아니라 감시 품질 경고입니다. 거래소 차트를 직접 확인하십시오."
    ))
    try:
        with ledger.db:
            ledger.db.execute("UPDATE video_system_notices SET status=? WHERE notice_key=?",
                              (result, key))
    except sqlite3.Error as exc:
        LOG.warning("VIDEO_FEED_WARNING_FINISH_FAILURE type=%s", type(exc).__name__)
    return result
