"""ZB observational 5m price-path alerts (A rejection / B early fall / C breakout).

All thresholds use previously closed, contiguous LBank-derived 5m bars.
A warning is not an entry recommendation or an order. Inferred closes are
not independently exchange-REST verified. Never backfill late/replayed alerts.
"""
from datetime import datetime, timedelta, timezone
import logging
import math
import sqlite3
from statistics import median

LOG = logging.getLogger("lbank_scenario_alerts")
KST = timezone(timedelta(hours=9))
MIN_BARS = 21
MAX_AGE = 30
COOLDOWN = 30 * 60
RETENTION = 7 * 86400
COST_RATE_ASSUMPTION = 0.0012
NAMES = {
    "A": "저항 접근 후 실패",
    "B": "저항 미도달 하방 이탈",
    "C": "저항 상향 돌파",
}
NEXT = {
    "A": "다음 5분봉의 저항 재돌파 여부와 거래량을 확인하세요.",
    "B": "이탈한 지지선의 재회복 여부를 확인하세요. 추격 숏은 금지합니다.",
    "C": "돌파 저항선의 재지지와 다음 15분봉 방향을 확인하세요.",
}


def _valid(rows):
    previous = None
    try:
        for r in rows:
            start = r["start"]
            if type(start) is not int or start % 300 or (
                previous is not None and start != previous + 300
            ):
                return False
            previous = start
            if r.get("quality") != "stream_closed" or r.get("period", 300) != 300:
                return False
            o, h, l, c, v = (float(r[k]) for k in
                             ("open", "high", "low", "close", "volume"))
            if not all(math.isfinite(x) for x in (o, h, l, c, v)):
                return False
            if l <= 0 or v < 0 or not l <= min(o, c) <= max(o, c) <= h:
                return False
            receipt = r.get("received_at")
            if type(receipt) not in (int, float) or receipt < start + 300:
                return False
    except (KeyError, TypeError, ValueError, OverflowError):
        return False
    return True


def evaluate_scenario(bars):
    """A deterministic event-only price-path observation with pre-bar levels."""
    if len(bars) < MIN_BARS:
        return {"status": "insufficient_history", "available": len(bars)}
    rows = bars[-MIN_BARS:]
    if not _valid(rows):
        return {"status": "invalid_source"}
    history = rows[:-1]
    current = rows[-1]
    close, open_ = float(current["close"]), float(current["open"])
    high, vol = float(current["high"]), float(current["volume"])
    resistance = max(float(x["high"]) for x in history[-12:])
    support = min(float(x["low"]) for x in history[-6:])
    vol_base = median(float(x["volume"]) for x in history)
    if vol_base <= 0:
        return {"status": "invalid_volume_baseline"}
    multiple = vol / vol_base

    true_ranges = []
    for i in range(len(history) - 14, len(history)):
        prior_close = float(history[i - 1]["close"])
        b = history[i]
        true_ranges.append(max(
            float(b["high"]) - float(b["low"]),
            abs(float(b["high"]) - prior_close),
            abs(float(b["low"]) - prior_close),
        ))
    atr = sum(true_ranges) / len(true_ranges)
    buffer = max(close * 0.00035, atr * 0.25)
    label = None
    reason = "no_scenario"
    if close > resistance + 0.1 * buffer and close > open_:
        if multiple >= 1.5:
            label = "C"
        else:
            reason = "breakout_volume_insufficient"
    elif high >= resistance - buffer and close < resistance - buffer and close < open_:
        if multiple >= 1.2:
            label = "A"
        else:
            reason = "rejection_volume_insufficient"
    elif close < support - 0.1 * buffer and close < open_:
        # B is a missed-resistance fall, not a late reaction after touching R.
        if max(float(x["high"]) for x in rows[-3:]) >= resistance - buffer:
            reason = "recent_resistance_touch"
        elif multiple >= 1.5:
            label = "B"
        else:
            reason = "downbreak_volume_insufficient"
    if label is None:
        return {"status": reason}

    if label == "A":
        stop = max(high, resistance) + buffer
    elif label == "B":
        stop = max(support, max(float(x["high"]) for x in rows[-3:])) + buffer
    else:
        stop = resistance - buffer

    if label in ("A", "B"):
        low = min(float(x["low"]) for x in history)
        target = low + 0.2 * atr if low + 0.2 * atr < close else None
        loss = stop - close
        reward = close - target if target is not None else None
    else:
        upper = max(float(x["high"]) for x in history)
        target = upper - 0.2 * atr if upper - 0.2 * atr > close else None
        loss = close - stop
        reward = target - close if target is not None else None

    cost = close * COST_RATE_ASSUMPTION
    net_rr = (max(0, reward - cost) / (loss + cost)
              if reward is not None and loss > 0 else None)
    return {
        "status": "candidate", "scenario": label, "start": current["start"],
        "close": close, "resistance": resistance, "support": support,
        "volume_multiple": multiple, "atr14": atr, "stop": stop,
        "structural_target": target, "assumed_net_rr": net_rr,
        "target_qualified": net_rr is not None and net_rr >= 2.0,
    }


def format_scenario(event):
    kind = event["scenario"]
    close_time = datetime.fromtimestamp(event["start"] + 300, KST).strftime("%m-%d %H:%M KST")
    stop = event["stop"]
    target = event["structural_target"]
    if target is None:
        target_line = "구조 목표 후보: 확인 불가 (손익비 검증 안 됨)"
    else:
        rr = event["assumed_net_rr"]
        target_line = (
            f"구조 목표 후보: {target:,.1f} USDT\n"
            f"가정 비용 후 손익비: {rr:.2f}:1 · "
            f"{'2:1 이상이나 진입 신호 아님' if event['target_qualified'] else '2:1 미달, 진입 부적격'}"
        )
    return (
        "[ZB 엘뱅크 BTCUSDT · 5분봉 시나리오 관찰]\n"
        "⚠️ 움직임 감지 전용 · 매매 진입 신호 아님 · 자동 주문 없음\n"
        f"시나리오 {kind}: {NAMES[kind]}\n"
        f"완료봉: {close_time}\n"
        f"완료봉 종가(체결가 아님): {event['close']:,.1f} USDT\n"
        f"이전 12봉 저항: {event['resistance']:,.1f} / 이전 6봉 지지: "
        f"{event['support']:,.1f} USDT\n"
        f"거래량: 앞선 20봉 중앙값 대비 {event['volume_multiple']:.2f}배\n"
        f"참고 무효 관찰선: {stop:,.1f} USDT (확정 손절 주문가 아님)\n"
        f"{target_line}\n"
        f"다음 확인: {NEXT[kind]}\n"
        "가정 비용: 왕복 수수료·슬리피지 합계 0.12%, 실제 비용과 다름.\n"
        "가격·지지·저항은 매 완료봉마다 새로 산정됩니다. 이전 수치를 고정 적용하지 마세요.\n"
        "봉은 엘뱅크 1분 스트림 합산 추정으로, 독립 REST 검증·체결 검증은 없습니다.\n"
        "주문 전 실제 호가와 손익비를 별도로 확인하세요."
    )


def send_scenario_candidate(ledger, bars, *, now, started, final_status, send, enabled):
    """At most one event per scenario per 30 min, reserved BEFORE attempting send."""
    if not enabled or send is None:
        return "disabled"
    if not bars or len(bars) < MIN_BARS:
        return "insufficient_history"
    last = bars[-1]
    close_at = last["start"] + 300
    received = last.get("received_at")
    if (type(received) not in (int, float) or
            close_at <= started or
            not close_at <= received <= now <= close_at + MAX_AGE):
        return "stale_or_before_start"
    if final_status == "telegram_accepted":
        return "formal_alert_priority"
    event = evaluate_scenario(bars)
    if event["status"] != "candidate":
        return event["status"]
    scenario = event["scenario"]
    try:
        with ledger.db:
            ledger.db.execute("""CREATE TABLE IF NOT EXISTS video_scenario_delivery (
                bar_start INTEGER NOT NULL,
                scenario TEXT NOT NULL,
                reserved_at REAL NOT NULL,
                status TEXT NOT NULL,
                PRIMARY KEY (bar_start, scenario)
            )""")
            ledger.db.execute("DELETE FROM video_scenario_delivery WHERE bar_start<?",
                              (close_at-RETENTION,))
            if ledger.db.execute(
                "SELECT 1 FROM video_scenario_delivery WHERE scenario=? AND bar_start>? LIMIT 1",
                (scenario, last["start"] - COOLDOWN),
            ).fetchone():
                return "cooldown"
            inserted = ledger.db.execute(
                "INSERT OR IGNORE INTO video_scenario_delivery VALUES(?,?,?,'reserved')",
                (last["start"], scenario, now),
            ).rowcount
            if not inserted:
                return "already_reserved"
    except sqlite3.Error as exc:
        LOG.warning("VIDEO_SCENARIO_RESERVATION_FAILURE type=%s", type(exc).__name__)
        return "reservation_failed"
    try:
        accepted = bool(send(format_scenario(event)))
    except Exception as exc:
        LOG.warning("VIDEO_SCENARIO_SEND_FAILURE type=%s", type(exc).__name__)
        accepted = False
    status = "accepted" if accepted else "unconfirmed"
    try:
        with ledger.db:
            ledger.db.execute(
                "UPDATE video_scenario_delivery SET status=? WHERE bar_start=? AND scenario=?",
                (status, last["start"], scenario),
            )
    except sqlite3.Error as exc:
        LOG.warning("VIDEO_SCENARIO_FINISH_FAILURE type=%s", type(exc).__name__)
    return f"{scenario}:{status}"

