"""Fast market movement observations independent of formal LBank entry screening.

1m momentum observes three fully CLOSED minute bars; 5m fallback observes a
newly closed bar. All reference levels precede each event. A low-volume price
move can create an OBSERVATION, not a trade approval. No orders, no entry prices.
"""
from datetime import datetime, timedelta, timezone
import logging
import math
import sqlite3
import time
from statistics import median

from lbank_b_early_1m_shadow import _valid_minutes
from lbank_scenario_alerts import _valid as _valid_fives

LOG = logging.getLogger("lbank_price_momentum_watch")
KST = timezone(timedelta(hours=9))
HISTORY = 21
COOLDOWN = 15 * 60
RETENTION = 7 * 86400
MAX_AGE = 30


def _atr(history, size=14):
    tail = history[-(size+1):]
    if len(tail) < size+1:
        return None
    tr = []
    for prev, cur in zip(tail, tail[1:]):
        tr.append(max(float(cur["high"])-float(cur["low"]),
                      abs(float(cur["high"])-float(prev["close"])),
                      abs(float(cur["low"])-float(prev["close"]))))
    return sum(tr) / len(tr)


def _candidate(bars, period):
    if len(bars) < HISTORY:
        return {"status": "insufficient_history"}
    rows = bars[-HISTORY:]
    valid = _valid_minutes(rows) if period == 60 else _valid_fives(rows)
    if not valid:
        return {"status": "invalid_source"}
    last, history = rows[-1], rows[:-1]
    baseline = median(float(x["volume"]) for x in history)
    atr = _atr(history)
    if not baseline or atr is None or atr <= 0 or not math.isfinite(atr):
        return {"status": "invalid_volatility_or_volume_baseline"}
    close = float(last["close"])
    if period == 60:
        # A 3-minute surge can be reported before the 5m candle closes.
        lookback = history[-6:]
        opening = float(rows[-3]["open"])
        threshold = max(close * .0012, 1.5 * atr)
        amount = close-opening
        # Last minute must still move in the observed direction.
        impulse_up = (amount >= threshold and close > float(last["open"])
              and close > max(float(x["high"]) for x in lookback) + close*.00005)
        impulse_down = (amount <= -threshold and close < float(last["open"])
                and close < min(float(x["low"]) for x in lookback) - close*.00005)
        # A sharp first/second minute followed by smaller but persistent closes
        # can be missed by a 3-minute-only impulse test. Detect the COMPLETED
        # five-minute staircase independently, never using an open candle.
        # This lane is informational, not an entry approval or loss estimate.
        recent = rows[-5:]
        five_open = float(recent[0]["open"])
        five_move = close - five_open
        five_minimum = max(close*.0018, 2.0*atr)
        positive = sum(float(x["close"]) > float(x["open"]) for x in recent)
        negative = sum(float(x["close"]) < float(x["open"]) for x in recent)
        prior_high_8 = max(float(x["high"]) for x in history[-8:])
        prior_low_8 = min(float(x["low"]) for x in history[-8:])
        staircase_up = (positive >= 4 and five_move >= five_minimum
            and close > float(last["open"])
            and close > prior_high_8 + close*.00005)
        staircase_down = (negative >= 4 and -five_move >= five_minimum
            and close < float(last["open"])
            and close < prior_low_8 - close*.00005)
        up, down = impulse_up or staircase_up, impulse_down or staircase_down
        trigger_type = ("three_minute_impulse" if impulse_up or impulse_down
            else "five_minute_staircase")
        if (staircase_up or staircase_down) and not (impulse_up or impulse_down):
            opening, amount = five_open, five_move
    else:
        # 5m fallback catches the 14:15 and 16:50 cases: a short-horizon
        # resistance break plus large candle body, independently of volume.
        opening = float(last["open"])
        threshold = max(close * .0012, .8 * atr)
        amount = close-opening
        lookback = history[-3:]
        up = (amount >= threshold
              and close > max(float(x["high"]) for x in lookback) + close*.00005)
        down = (amount <= -threshold
                and close < min(float(x["low"]) for x in lookback) - close*.00005)
        trigger_type = "five_minute_breakout"
    if not (up or down):
        return {"status": "no_fast_price_structure"}
    ratio = float(last["volume"])/baseline
    return {
        "status": "watch", "direction": "up" if up else "down",
        "trigger_type": trigger_type,
        "start": int(last["start"]), "period": period, "close": close,
        "opening": opening, "reference": (
            max(float(x["high"]) for x in lookback) if up else
            min(float(x["low"]) for x in lookback)),
        "volume_multiple": ratio, "volume_warning": ratio < 1.5,
        "movement": amount, "atr": atr,
        "buffer": close*.00005,
        "note": "fast_observation_not_an_entry",
    }


def evaluate_minute_watch(minutes):
    return _candidate(minutes, 60)


def evaluate_five_watch(fives):
    return _candidate(fives, 300)


def format_watch(event):
    period = event["period"]
    stamp = datetime.fromtimestamp(event["start"]+period, KST).strftime("%m-%d %H:%M KST")
    rising = event["direction"]=="up"
    side = "상승" if rising else "하락"
    risk = ("낮은 거래량 · 돌파 실패·급반전 위험 높음"
            if event["volume_warning"] else
            "거래량은 별도 검증 필요 · 급반전 위험 유의")
    return (
        f"[LBank BTCUSDT | {period//60}분봉 빠른 가격 움직임 관찰 | {stamp}]\n"
        f"조기 방향 경고: {side} ({'롱' if rising else '숏'} 방향 참고)\n"
        "판정: 가격 움직임 포착 · 매매 진입 적격 미확정\n"
        f"기존 {'저항' if rising else '지지'} {event['reference']:,.1f} USDT"
        f" / 확인 종가 {event['close']:,.1f} USDT\n"
        f"최근 가속폭 {event['movement']:+,.1f} USDT"
        f" / 거래량 이전 기준 대비 {event['volume_multiple']:.2f}배\n"
        f"감지 경로: {event.get('trigger_type', 'price_break')}\n"
        f"주의: {risk}\n"
        "진입가·손절·목표: 미승인 (호가·구조 목표·비용 차감 손익비 검증 전)\n"
        "이 경고는 ‘대기’보다 이른 관찰을 위한 별도 경로입니다.\n"
        "5분봉 확정·호가·손익비·추세 검증은 독립적으로 유지됩니다.\n"
        "※ 정식 진입 신호 아님 · 50배 손실/청산 위험 · 자동주문 없음\n"
        "※ LBank 1분 스트림 기반 완료봉, REST 독립 검증 전"
    )


def send_price_watch(ledger, bars, *, period, now, started, send, enabled):
    if not enabled or send is None:
        return "disabled"
    if not bars or len(bars) < HISTORY:
        return "insufficient_history"
    last = bars[-1]
    end = last.get("start", 0) + period
    receipt = last.get("received_at")
    if (type(receipt) not in (int, float) or
            not end <= receipt <= now <= end+MAX_AGE or end <= started):
        return "stale_or_before_start"
    event = evaluate_minute_watch(bars) if period==60 else (
        evaluate_five_watch(bars) if period==300 else {"status": "invalid_period"})
    if event["status"] != "watch":
        return event["status"]
    stamp, side = event["start"], event["direction"]
    with ledger.db:
        ledger.db.execute("""CREATE TABLE IF NOT EXISTS video_price_momentum_notice(
            bar_start INTEGER NOT NULL, period INTEGER NOT NULL,
            direction TEXT NOT NULL, reported_at REAL NOT NULL,
            status TEXT NOT NULL, PRIMARY KEY(bar_start,period,direction))""")
        ledger.db.execute("DELETE FROM video_price_momentum_notice WHERE bar_start<?",
                          (stamp-RETENTION,))
        previous=ledger.db.execute("""SELECT 1 FROM video_price_momentum_notice
            WHERE direction=? AND bar_start>? LIMIT 1""",
            (side,stamp-COOLDOWN)).fetchone()
        if previous:
            return "cooldown"
        # A conventional 1m prealert already carries the same direction;
        # avoid duplicate 1m OR 5m watch notices for that prealert.
        if period in (60, 300):
            try:
                previous=ledger.db.execute("""SELECT 1 FROM video_one_minute_notice
                    WHERE direction=? AND bar_start>? AND status IN ('reserved','accepted','unconfirmed')
                    LIMIT 1""",(side,stamp-COOLDOWN)).fetchone()
                if previous:
                    return "existing_minute_prealert"
            except sqlite3.OperationalError:
                pass
        if not ledger.db.execute("""INSERT OR IGNORE INTO video_price_momentum_notice
            VALUES(?,?,?,?,'reserved')""",(stamp,period,side,now)).rowcount:
            return "already_reserved"
    ok=False
    send_started = time.monotonic()
    try:
        ok=bool(send(format_watch(event)))
    except Exception as exc:
        LOG.warning("VIDEO_PRICE_MOMENTUM_SEND_FAILURE type=%s",type(exc).__name__)
    status="accepted" if ok else "unconfirmed_no_retry"
    LOG.info("VIDEO_PRICE_MOMENTUM_TIMING period=%d bar=%d direction=%s trigger=%s "
             "close_to_check_s=%.2f send_to_api_response_s=%.2f api_accepted=%s "
             "device_receipt_verified=false",
             period, stamp, side, event["trigger_type"],
             max(0., now-(stamp+period)), time.monotonic()-send_started, ok)
    try:
        with ledger.db:
            ledger.db.execute("""UPDATE video_price_momentum_notice SET status=?
                WHERE bar_start=? AND period=? AND direction=?""",
                (status,stamp,period,side))
    except sqlite3.Error:
        LOG.warning("VIDEO_PRICE_MOMENTUM_LEDGER_UPDATE_FAILED")
    LOG.info("VIDEO_PRICE_MOMENTUM_NOTICE period=%d bar=%d direction=%s "
             "volume_multiple=%.2f status=%s entry_approved=false",
             period,stamp,side,event["volume_multiple"],status)
    return f"{side}:{status}"
