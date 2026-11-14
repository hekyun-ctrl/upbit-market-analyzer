"""LBank BTCUSDT *one-minute* breakout heads-up, independent of 5m entry.

Observation-only notice. Never a formal entry signal or an order.
Only the newest completed, contiguous native 1m stream-inferred bars are used.
All levels are pre-signal and no future candles are consulted. Telegram API
acceptance is logged independently of receipt on the user's device.
"""
from datetime import datetime, timedelta, timezone
import logging
import math
import sqlite3
import time
from statistics import median

from lbank_b_early_1m_shadow import _valid_minutes

LOG = logging.getLogger("lbank_one_minute_prealert")
KST = timezone(timedelta(hours=9))
MIN_BARS = 21
MAX_CLOSE_AGE = 30
COOLDOWN_SECONDS = 15 * 60
RETENTION_SECONDS = 7 * 86400
POLICY = "native_1m_observation_v1"


def evaluate_one_minute_move(minutes):
    """Detect a fresh close beyond PRE-MINUTE 12-bar high/low.

    No five-minute warmup or higher timeframe may block the informational
    notice. It does *not* assert that a profitable/qualified trade exists.
    """
    if len(minutes) < MIN_BARS:
        return {"status": "insufficient_history"}
    rows = minutes[-MIN_BARS:]
    if not _valid_minutes(rows):
        return {"status": "invalid_source"}
    history, last = rows[:-1], rows[-1]
    base_volume = median(float(b["volume"]) for b in history)
    if base_volume <= 0:
        return {"status": "invalid_volume_baseline"}
    movement = abs(float(last["close"]) - float(last["open"]))
    tr = []
    for prev, bar in zip(rows[-16:-2], rows[-15:-1]):
        tr.append(max(float(bar["high"])-float(bar["low"]),
                      abs(float(bar["high"])-float(prev["close"])),
                      abs(float(bar["low"])-float(prev["close"]))))
    atr = sum(tr)/len(tr) if tr else 0
    if atr <= 0 or not math.isfinite(atr):
        return {"status": "invalid_volatility"}
    close, op = float(last["close"]), float(last["open"])
    prior_low = min(float(b["low"]) for b in history[-12:])
    prior_high = max(float(b["high"]) for b in history[-12:])
    buffer = max(close * .00010, atr * .10)
    minimum_body = max(close * .00075, atr * .60)
    ratio = float(last["volume"])/base_volume
    if movement < minimum_body:
        return {"status": "move_too_small"}
    if ratio < 1.3:
        return {"status": "volume_below_threshold"}
    if close < prior_low - buffer and close < op:
        direction, threshold = "down", prior_low
    elif close > prior_high + buffer and close > op:
        direction, threshold = "up", prior_high
    else:
        return {"status": "no_fresh_break"}
    return {
        "status": "candidate", "direction": direction,
        "start": last["start"], "close": close, "open": op,
        "prior_level": threshold, "buffer": buffer,
        "volume_multiple": ratio, "atr1": atr,
        "movement_pct": 100 * abs(close-op)/op,
        "large_move_warning": movement > 3 * atr,
    }


def format_one_minute_prealert(event, *, checked_at):
    end = event["start"] + 60
    bar_time = datetime.fromtimestamp(end, KST).strftime("%m-%d %H:%M:%S KST")
    checked_time = datetime.fromtimestamp(checked_at, KST).strftime("%H:%M:%S KST")
    down = event["direction"] == "down"
    return (
        "[ZB BTCUSDT · 1분봉 조기 시장변화 경보]\n"
        "⚠️ 조기 관찰 전용 · 정식 진입 신호 아님 · 자동주문 없음\n"
        f"방향: {'하방 지지 이탈' if down else '상방 저항 돌파'}\n"
        f"봉 종료: {bar_time} / 서버 판정: {checked_time}\n"
        f"LBank 1분봉 종가: {event['close']:,.1f} USDT (시장가 아님)\n"
        f"이전 12분 {'지지' if down else '저항'}: {event['prior_level']:,.1f} USDT\n"
        f"분봉 몸통 변화: {event['movement_pct']:.2f}%\n"
        f"거래량: 직전 20분 중앙값 대비 {event['volume_multiple']:.2f}배\n"
        f"{'⚠️ 이미 큰 변동 발생, 추격 금지' if event['large_move_warning'] else '후속 5분봉에서 방향과 손익비 재확인 필요'}\n"
        "1분봉도 마감 후 감지하므로 첫 움직임보다 최대 약 1분 늦을 수 있습니다.\n"
        "연속 LBank 스트림 마감 추정이며 독립 REST 원본 확인 전입니다.\n"
        "진입가·손절·목표·호가·비용 검증 전. 50배 선물 진입 권유 아님."
    )


def send_one_minute_prealert(ledger, minutes, *, now, started,
                             send, enabled, suppress=False, clock=time.time):
    """Reserve BEFORE send, report API acknowledgement and server latency."""
    if not enabled or send is None:
        return "disabled"
    if len(minutes) < MIN_BARS:
        return "insufficient_history"
    last = minutes[-1]
    start = last.get("start")
    if type(start) is not int:
        return "invalid_source"
    close_at = start + 60
    if close_at <= started:
        return "before_worker_start"
    receipt = last.get("received_at")
    if (type(receipt) not in (int,float)
            or not close_at <= receipt <= now <= close_at + MAX_CLOSE_AGE
            or any(type(b.get("received_at")) not in (int,float)
                   or b["received_at"] > now for b in minutes[-MIN_BARS:])):
        return "stale_or_unverified"
    if suppress:
        return "higher_priority_alert"
    event = evaluate_one_minute_move(minutes)
    if event["status"] != "candidate":
        return event["status"]
    direction = event["direction"]
    try:
        with ledger.db:
            ledger.db.execute("""CREATE TABLE IF NOT EXISTS video_one_minute_notice(
                bar_start INTEGER NOT NULL,
                direction TEXT NOT NULL,
                reserved_at REAL NOT NULL,
                status TEXT NOT NULL,
                sent_at REAL,
                PRIMARY KEY(bar_start, direction))""")
            ledger.db.execute("DELETE FROM video_one_minute_notice WHERE bar_start<?",
                              (start-RETENTION_SECONDS,))
            if ledger.db.execute(
                """SELECT 1 FROM video_one_minute_notice
                   WHERE direction=? AND bar_start>? LIMIT 1""",
                (direction, start-COOLDOWN_SECONDS)).fetchone():
                return "cooldown"
            if not ledger.db.execute(
                """INSERT OR IGNORE INTO video_one_minute_notice
                   VALUES(?,?,?,'reserved',NULL)""",
                (start,direction,now)).rowcount:
                return "already_reserved"
    except sqlite3.Error as exc:
        LOG.warning("VIDEO_1M_NOTICE_LEDGER_FAILURE type=%s",type(exc).__name__)
        return "reservation_failed"
    checked_at=clock()
    accepted=False
    try:
        accepted=bool(send(format_one_minute_prealert(event,checked_at=checked_at)))
    except Exception as exc:
        LOG.warning("VIDEO_1M_NOTICE_SEND_FAILURE type=%s",type(exc).__name__)
    sent_at=clock()
    result="accepted" if accepted else "unconfirmed"
    LOG.info(
        "VIDEO_1M_NOTICE_TIMING minute=%d direction=%s status=%s "
        "close_to_evaluate_s=%.2f evaluate_to_api_ack_s=%.2f total_s=%.2f",
        start,direction,result,max(0,checked_at-close_at),
        max(0,sent_at-checked_at),max(0,sent_at-close_at))
    try:
        with ledger.db:
            ledger.db.execute(
                """UPDATE video_one_minute_notice SET status=?,sent_at=?
                   WHERE bar_start=? AND direction=?""",
                (result,sent_at,start,direction))
    except sqlite3.Error as exc:
        LOG.warning("VIDEO_1M_NOTICE_FINISH_FAILURE type=%s",type(exc).__name__)
    return f"{direction}:{result}"
