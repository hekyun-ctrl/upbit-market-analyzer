"""Causal, observational BTC 5m W-bottom lifecycle; never an order or formal entry.

Stages: defended second low -> rebound -> neckline breakout -> sustained close.
Frozen levels, receipt-checked closed candles, reserved delivery before Telegram.
No retroactive notifications after a worker restart. All targets are hypotheses.
"""
from datetime import datetime, timedelta, timezone
import logging
import math
import sqlite3
from statistics import median

LOG = logging.getLogger("lbank_double_bottom_alerts")
KST = timezone(timedelta(hours=9))
MIN_BARS = 32
WINDOW = 65
MAX_AGE = 30
COOLDOWN = 30 * 60
RETENTION = 7 * 86400
ROUND_TRIP_COST = .0012
PHASES = {"defense": "두 번째 저점 방어", "rebound": "저점 이후 반등 확인",
          "breakout": "넥라인 상향 돌파", "hold": "넥라인 돌파 유지",
          "invalidated": "패턴 무효화"}


def _valid(bars):
    last = None
    try:
        for bar in bars:
            start = bar["start"]
            if type(start) is not int or start % 300 or (
                    last is not None and start != last + 300):
                return False
            last = start
            if bar.get("quality") != "stream_closed" or bar.get("period", 300) != 300:
                return False
            o, h, l, c, v = (float(bar[k]) for k in
                              ("open", "high", "low", "close", "volume"))
            receipt = bar.get("received_at")
            if not (all(math.isfinite(x) for x in (o, h, l, c, v))
                    and 0 < l <= min(o, c) <= max(o, c) <= h and v >= 0
                    and type(receipt) in (int, float)
                    and math.isfinite(receipt) and receipt >= start + 300):
                return False
    except (KeyError, TypeError, ValueError, OverflowError):
        return False
    return True


def _atr(rows):
    trs = []
    for prev, bar in zip(rows[-16:-1], rows[-15:]):
        trs.append(max(bar["high"]-bar["low"],
                       abs(bar["high"]-prev["close"]),
                       abs(bar["low"]-prev["close"])))
    return sum(trs)/len(trs) if trs else 0


def _pivot(rows, i):
    return (i >= 2 and i+1 < len(rows)
            and rows[i]["low"] < rows[i-1]["low"]
            and rows[i]["low"] <= rows[i-2]["low"]
            and rows[i]["low"] <= rows[i+1]["low"])


def detect_double_bottom(bars):
    """Pure observation. Pivots require a *later completed bar*, not future data."""
    if len(bars) < MIN_BARS:
        return {"status": "insufficient_history"}
    rows = bars[-WINDOW:]
    if not _valid(rows):
        return {"status": "invalid_source"}
    atr = _atr(rows)
    if not math.isfinite(atr) or atr <= 0:
        return {"status": "invalid_atr"}
    price = rows[-1]["close"]
    tolerance = max(price * .0025, atr * .75)
    minimum_height = max(price * .002, atr * 1.1)
    last = len(rows)-1
    # Most recent confirmed second bottom wins. Both pivots must be observed
    # before the present bar; no later-price labels or look-ahead.
    for j in range(last-1, max(3, last-18), -1):
        if not _pivot(rows, j):
            continue
        if last - j > 18:
            continue
        for i in range(j-4, max(2, j-25)-1, -1):
            if not _pivot(rows, i):
                continue
            low1, low2 = rows[i]["low"], rows[j]["low"]
            if abs(low1-low2) > tolerance:
                continue
            neckline = max(row["high"] for row in rows[i+1:j])
            height = neckline - (low1 + low2)/2
            if height < minimum_height or height > price * .025:
                continue
            # Price must have declined into the first bottom.
            if max(row["high"] for row in rows[max(0,i-6):i]) < low1 + max(.0015*price, .8*atr):
                continue
            buffer = max(price*.00035, atr*.20)
            if any(row["close"] < min(low1, low2)-buffer for row in rows[j+1:]):
                continue
            if rows[-1]["close"] < low2 or rows[-1]["close"] > neckline+buffer:
                continue
            return {"status": "candidate", "key": str(rows[j]["start"]),
                    "low1_start": rows[i]["start"], "low2_start": rows[j]["start"],
                    "low1": low1, "low2": low2, "neckline": neckline,
                    "atr": atr, "volume_softened": rows[j]["volume"] < rows[i]["volume"],
                    "volume_ratio": rows[j]["volume"]/rows[i]["volume"] if rows[i]["volume"]>0 else None}
    return {"status": "no_confirmed_w"}


def _setup(db):
    db.execute("""CREATE TABLE IF NOT EXISTS video_double_bottom_stages(
        pattern_key TEXT PRIMARY KEY,
        low1_start INTEGER NOT NULL,
        low2_start INTEGER NOT NULL,
        low1 REAL NOT NULL, low2 REAL NOT NULL, neckline REAL NOT NULL,
        atr REAL NOT NULL,
        volume_softened INTEGER NOT NULL, volume_ratio REAL,
        stage TEXT NOT NULL, stage_at INTEGER NOT NULL,
        breakout_at INTEGER,
        created_at REAL NOT NULL, last_delivery TEXT NOT NULL)""")
    db.execute("DELETE FROM video_double_bottom_stages WHERE low2_start<?",
               (int(db.execute("SELECT COALESCE(MAX(low2_start),0) FROM video_double_bottom_stages").fetchone()[0])-RETENTION,))


def _next_stage(state, bars):
    last = bars[-1]
    close, prev = last["close"], bars[-2]["close"]
    neck, atr = state["neckline"], state["atr"]
    low = min(state["low1"],state["low2"])
    buffer = max(neck*.00035, atr*.20)
    if close < low-buffer or (
            state["stage"] in ("breakout","hold") and close < neck-buffer):
        return "invalidated"
    if last["start"]-state["low2_start"] > 18*300:
        return "expired"
    if state["stage"] == "breakout":
        if (last["start"] >= state["breakout_at"]+2*300
                and bars[-2]["close"] >= neck-buffer and close >= neck-buffer):
            return "hold"
        return "monitoring_hold"
    if state["stage"] == "hold":
        return "hold_monitoring"
    volume_base = median(x["volume"] for x in bars[-21:-1])
    if (close > neck+buffer and prev <= neck+buffer
            and volume_base > 0 and last["volume"] >= volume_base*1.25):
        return "breakout"
    if close > neck+buffer:
        return "breakout_volume_unconfirmed"
    if (state["stage"] == "defense"
            and close >= state["low2"] + max(.35*(neck-state["low2"]), .65*atr)):
        return "rebound"
    return "monitoring_rebound"


def format_double_bottom(state, phase, bar):
    stamp = datetime.fromtimestamp(bar["start"]+300, KST).strftime("%m-%d %H:%M KST")
    neck, low = state["neckline"], min(state["low1"], state["low2"])
    close = bar["close"]
    atr = state["atr"]
    stop = low-max(.35*atr,low*.00035)
    target = neck + (neck - (state["low1"]+state["low2"])/2)
    risk = close-stop + close*ROUND_TRIP_COST
    gain = target-close-close*ROUND_TRIP_COST
    rr = max(0,gain)/risk if risk > 0 else 0
    note = {
        "defense": "첫 반등 고점(넥라인)을 아직 넘지 않았습니다. 조기 관찰 단계입니다.",
        "rebound": "두 번째 저점에서 반등했습니다. 넥라인 접근을 확인하세요.",
        "breakout": "돌파봉이 나왔습니다. 다음 완료봉들의 넥라인 지지를 확인하세요.",
        "hold": "돌파 후 두 개 이상의 완료봉이 넥라인 위를 지켰습니다.",
        "invalidated": "이전 바닥/넥라인 방어 기준이 깨졌습니다. 이전 후보를 취소하세요.",
    }[phase]
    return (
        "[ZB BTCUSDT · W 쌍바닥 단계 관찰 / 실험]\n"
        "⚠️ 정식 진입 알람 아님 · 수익성 검증 전 · 자동 주문 없음\n"
        f"단계: {PHASES[phase]} | {stamp}\n"
        f"LBank 5분 완료봉 종가: {close:,.1f} USDT\n"
        f"1차 저점: {state['low1']:,.1f} / 2차 저점: {state['low2']:,.1f}\n"
        f"넥라인(두 저점 사이 고점): {neck:,.1f} USDT\n"
        f"두 번째 저점 거래량 감소: {'예' if state['volume_softened'] else '아니오/미확인'}\n"
        f"관찰 무효화 가격대: {stop:,.1f} USDT (확정 손절가 아님)\n"
        f"측정폭 목표 *가설*: {target:,.1f} USDT\n"
        f"가정 비용 후 손익비: {rr:.2f}:1 · "
        f"{'2:1 이상, 실제 체결 검증 필요' if rr>=2 else '2:1 미달, 진입 부적격'}\n"
        f"다음 판단: {note}\n"
        "왕복 비용 0.12% 가정. 목표 도달 확률과 실제 수익성은 검증되지 않았습니다.\n"
        "LBank 1분 스트림 합성 5분봉으로 독립 REST 원본 대조 전입니다."
    )


def send_double_bottom_candidate(ledger, bars, *, now, started,
                                 final_status, send, enabled):
    """Emit each phase once, reserve before sending; fail closed on gaps/stale."""
    if not enabled or send is None:
        return "disabled"
    if len(bars) < MIN_BARS:
        return "insufficient_history"
    tail = bars[-WINDOW:]
    if not _valid(tail):
        return "invalid_source"
    last = tail[-1]
    end = last["start"]+300
    if (end <= started or not end <= last["received_at"] <= now <= end+MAX_AGE
            or any(row["received_at"] > now for row in tail)):
        return "stale_or_before_start"
    if final_status == "telegram_accepted":
        return "formal_alert_priority"
    try:
        with ledger.db:
            _setup(ledger.db)
            ledger.db.row_factory = sqlite3.Row
            active = ledger.db.execute(
                """SELECT * FROM video_double_bottom_stages
                   WHERE stage NOT IN ('expired','invalidated')
                   ORDER BY low2_start DESC LIMIT 1""").fetchone()
            active = dict(active) if active else None
            if active is not None and last["start"] - active["low2_start"] > 18*300:
                ledger.db.execute("UPDATE video_double_bottom_stages SET stage='expired' WHERE pattern_key=?",
                                  (active["pattern_key"],))
                active = None
            if active is not None:
                phase = _next_stage(active, tail)
                if phase == "expired":
                    ledger.db.execute(
                        "UPDATE video_double_bottom_stages SET stage='expired' WHERE pattern_key=?",
                        (active["pattern_key"],))
                    return "expired"
                if phase not in PHASES:
                    return phase
                if active["stage_at"] == last["start"]:
                    return "already_reserved"
                if phase == "invalidated" and active["stage"] == "invalidated":
                    return "already_invalidated"
                if phase == active["stage"]:
                    return "already_notified"
            else:
                detected = detect_double_bottom(tail)
                if detected["status"] != "candidate":
                    return detected["status"]
                key = detected["key"]
                if ledger.db.execute(
                    "SELECT 1 FROM video_double_bottom_stages WHERE pattern_key=?",
                    (key,)).fetchone():
                    return "already_pattern"
                if ledger.db.execute(
                    "SELECT 1 FROM video_double_bottom_stages WHERE created_at>? LIMIT 1",
                    (now-COOLDOWN,)).fetchone():
                    return "new_pattern_cooldown"
                active = dict(pattern_key=key,
                    low1_start=detected["low1_start"], low2_start=detected["low2_start"],
                    low1=detected["low1"],low2=detected["low2"],
                    neckline=detected["neckline"],atr=detected["atr"],
                    volume_softened=int(detected["volume_softened"]),
                    volume_ratio=detected["volume_ratio"],stage="defense",
                    stage_at=last["start"],breakout_at=None,
                    created_at=now,last_delivery="reserved")
                ledger.db.execute(
                    "INSERT INTO video_double_bottom_stages VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (key,active["low1_start"],active["low2_start"],
                     active["low1"],active["low2"],active["neckline"],active["atr"],
                     active["volume_softened"],active["volume_ratio"],"defense",
                     last["start"],None,now,"reserved"))
                phase = "defense"
            if phase != "defense":
                # Freeze all structural anchors from first observation.
                ledger.db.execute(
                    """UPDATE video_double_bottom_stages
                       SET stage=?, stage_at=?, breakout_at=CASE
                           WHEN ?='breakout' THEN ? ELSE breakout_at END,
                           last_delivery='reserved' WHERE pattern_key=?""",
                    (phase,last["start"],phase,last["start"],active["pattern_key"]))
                active["stage"] = phase
                active["stage_at"] = last["start"]
                if phase=="breakout":
                    active["breakout_at"]=last["start"]
    except sqlite3.Error as exc:
        LOG.warning("VIDEO_DB_LEDGER_ERROR type=%s",type(exc).__name__)
        return "ledger_error"
    try:
        ok=bool(send(format_double_bottom(active,phase,last)))
    except Exception as exc:
        LOG.warning("VIDEO_DB_SEND_ERROR type=%s",type(exc).__name__)
        ok=False
    state="accepted" if ok else "unconfirmed"
    try:
        with ledger.db:
            ledger.db.execute(
                "UPDATE video_double_bottom_stages SET last_delivery=? WHERE pattern_key=?",
                (state,active["pattern_key"]))
    except sqlite3.Error as exc:
        LOG.warning("VIDEO_DB_DELIVERY_FINISH_ERROR type=%s",type(exc).__name__)
    return f"{phase}:{state}"
