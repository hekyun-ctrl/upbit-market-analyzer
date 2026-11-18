"""Prioritized, cost-screened LBank BTCUSDT opportunity REVIEW notices.

This research lane is independent of the locked formal trade-delivery path.
It NEVER approves entries, trades an account, invents a price objective,
or re-labels an unverified historical model as proven profitable.
"""
from datetime import datetime, timedelta, timezone
import logging
import math
import sqlite3
import time

from lbank_loss_review_guard import parallel_observation
from lbank_b_retest_research import trend_priority
from lbank_structural_target_plan import available_barriers, rounded
from research.zerobase.video_entry import Costs, judge_entry

LOG = logging.getLogger("lbank_opportunity_review")
KST = timezone(timedelta(hours=9))
MAX_AGE = 30
MAX_BOOK_SECONDS = 5
MAX_SPREAD = .0005
COOLDOWN = 30 * 60
RETENTION = 7 * 86400
COSTS = Costs(min_rr=2.0)
NAMES = {"A": "저항 실패", "B": "지지 이탈", "C": "상방 돌파"}
STRUCTURE_MIN_BARS = 21
HIGHER_TIMEFRAME_MIN_BARS = 72


def evaluate_review(bars, *, now, get_book, clock=time.time):
    """Fail closed on stale candles, unknown target, bad quote, or costs."""
    if len(bars) < STRUCTURE_MIN_BARS:
        return {"status": "insufficient_structure_history",
                "required": STRUCTURE_MIN_BARS, "available": len(bars)}
    last = bars[-1]
    end = last.get("start", 0) + 300
    receipt = last.get("received_at")
    if (type(receipt) not in (int, float) or
            not end <= receipt <= now <= end + MAX_AGE):
        return {"status": "stale_or_unverified"}
    observed = parallel_observation(bars)
    if observed.get("status") != "observed":
        return {"status": observed.get("status", "invalid_observation")}
    # The independent 21-bar structure stage MUST NOT wait for 72 bars.
    # No 15m/1h consensus is inferred from a short or recently restarted feed.
    rank = (trend_priority(bars) if len(bars) >= HIGHER_TIMEFRAME_MIN_BARS
            else {"status": "insufficient_history", "trend": "unknown",
                  "directions": {}})
    if rank.get("status") not in ("ready", "insufficient_history",
                                   "insufficient_higher_timeframe"):
        return {"status": "higher_timeframe_source_invalid"}
    higher_ready = rank.get("status") == "ready"
    eligible = []
    for lane in ("B", "A", "C"):
        state = observed["lanes"].get(lane, {})
        if state.get("status") == "observed_not_entry":
            direction = 1 if lane == "C" else -1
            regime = rank["trend"]
            # An independently bearish structure may occur while the hourly
            # recovery is incomplete. Keep MIXED visible, but never assert
            # higher-timeframe confirmation. Opposite consensus is excluded.
            if (higher_ready and
                    (direction == -1 and regime == "bullish" or
                     direction == 1 and regime == "bearish")):
                continue
            eligible.append((lane, direction))
    if not eligible:
        return {"status": "no_high_priority_structure"}
    try:
        start = clock()
        bid, ask = get_book()
        checked = clock()
    except Exception:
        return {"status": "book_unavailable"}
    if (checked - start > MAX_BOOK_SECONDS or
            not all(type(v) in (int, float) and math.isfinite(v) for v in (bid, ask)) or
            not 0 < bid < ask or
            (ask-bid)/((ask+bid)/2) > MAX_SPREAD):
        return {"status": "stale_or_wide_book"}
    atr = float(observed["atr14"])
    buffer = max(.30 * atr, float(last["close"]) * .00035)
    rejected = []
    for lane, direction in eligible:
        quote = ask if direction == 1 else bid
        if abs(quote-float(last["close"])) > max(.35*atr, quote*.0015):
            rejected.append("quote_moved_after_close")
            continue
        if direction == -1:
            anchor = (max(float(last["high"]), float(observed["resistance"]))
                      if lane == "A" else
                      max(float(last["high"]), float(observed["support"])))
            stop = rounded(anchor + buffer, COSTS.tick, up=True)
        else:
            stop = rounded(min(float(observed["resistance"]),
                               float(last["low"])) - buffer, COSTS.tick, up=False)
        try:
            barriers = available_barriers(bars[-240:], direction, quote, checked)
        except (ValueError, KeyError, TypeError, OverflowError):
            rejected.append("target_source_invalid")
            continue
        if not barriers:
            rejected.append("no_known_target")
            continue
        barrier = barriers[0]
        target = rounded(float(barrier["price"]) -
                         direction * max(COSTS.tick, quote*.00015),
                         COSTS.tick, up=direction == -1)
        plan = {"lane": "opportunity_review", "direction": direction,
                "stop": stop, "target": target}
        verdict, rr = judge_entry(plan, quote, COSTS)
        if verdict != "qualified" or rr is None:
            rejected.append(verdict)
            continue
        review_grade = ("early_structure_only" if not higher_ready else
                        "higher_timeframe_mixed" if rank["trend"] == "mixed" else
                        "higher_timeframe_aligned")
        return {"status": "review_candidate", "lane": lane,
                "direction": direction, "start": last["start"],
                "review_grade": review_grade, "bars_seen": len(bars),
                "higher_timeframe_confirmed": bool(higher_ready and
                    rank["trend"] not in ("mixed", "unknown")),
                "close": float(last["close"]), "quote": quote,
                "stop": stop, "target": target, "net_rr_assumed": rr,
                "barrier": float(barrier["price"]),
                "barrier_available_at": barrier["available_at"],
                "atr14": atr, "volume_multiple": observed["volume_multiple"],
                "trend": rank["trend"], "higher_directions": rank["directions"],
                "spread_pct": 100*(ask-bid)/((ask+bid)/2)}
    return {"status": "screen_rejected",
            "reasons": list(dict.fromkeys(rejected))}


def format_review(event):
    stamp = datetime.fromtimestamp(event["start"]+300, KST).strftime("%m-%d %H:%M KST")
    side = "롱" if event["direction"] == 1 else "숏"
    direction = {"bearish":"하락 우세", "bullish":"상승 우세",
                 "mixed":"상위봉 충돌", "unknown":"확인 자료 부족"}[event["trend"]]
    early = event["review_grade"] == "early_structure_only"
    tier = ("조기 구조 후보 · 상위봉 미확인" if early else
            "구조 후보 · 상위봉 충돌" if event["review_grade"] == "higher_timeframe_mixed"
            else "고우선 구조 기회 검토")
    caveat = ("주의: 5분봉 21개부터 조기 포착. 15분·1시간 합의는 미검증.\n"
              if early else "")
    return (
        f"[BTCUSDT {tier} · {stamp}]\n"
        f"{caveat}"
        f"{event['lane']}형 / {side} 관찰 · {NAMES[event['lane']]}\n"
        f"발송 전 확인 호가 {event['quote']:,.1f} USDT (5분 종가 {event['close']:,.1f})\n"
        f"무효화 기준 가설 {event['stop']:,.1f} / 확인된 구조 목표 {event['target']:,.1f}\n"
        f"가정 비용 차감 손익비 {event['net_rr_assumed']:.2f} "
        f"(호가 스프레드 {event['spread_pct']:.3f}%)\n"
        f"거래량 {event['volume_multiple']:.2f}배 / 상위봉 {direction}"
        f" / 연속 5분봉 {event['bars_seen']}개\n"
        f"구조 목표의 근거는 이전 완료봉의 확정 가격 장벽 {event['barrier']:,.1f}\n"
        "주의: 실거래 성과 검증 중. 정식 진입 승인 아님 · 자동주문 없음.\n"
        "호가 변화·급반등·수수료·50배 청산위험을 별도로 확인하세요.\n"
        "LBank 1분 스트림 합산 5분봉이며 거래소 REST 독립 검증 전입니다."
    )


def send_review(ledger, bars, *, now, started, get_book, send, enabled,
                final_status, clock=time.time):
    if not enabled or send is None:
        return "disabled"
    if final_status == "telegram_accepted":
        return "formal_alert_priority"
    if not bars or bars[-1]["start"]+300 <= started:
        return "before_start"
    event = evaluate_review(bars, now=now, get_book=get_book, clock=clock)
    if event["status"] != "review_candidate":
        return event["status"]
    start, direction = event["start"], event["direction"]
    try:
        with ledger.db:
            ledger.db.execute("""CREATE TABLE IF NOT EXISTS
                video_opportunity_review_notice(
                  bar_start INTEGER NOT NULL, direction INTEGER NOT NULL,
                  reserved_at REAL NOT NULL, status TEXT NOT NULL,
                  PRIMARY KEY(bar_start, direction))""")
            ledger.db.execute(
                "DELETE FROM video_opportunity_review_notice WHERE bar_start<?",
                (start-RETENTION,))
            if ledger.db.execute("""SELECT 1 FROM video_opportunity_review_notice
                WHERE direction=? AND bar_start>? LIMIT 1""",
                (direction, start-COOLDOWN)).fetchone():
                return "cooldown"
            if not ledger.db.execute("""INSERT OR IGNORE INTO
                video_opportunity_review_notice VALUES(?,?,?,'reserved')""",
                (start, direction, now)).rowcount:
                return "already_reserved"
    except sqlite3.Error:
        return "ledger_unavailable"
    accepted = False
    try:
        accepted = bool(send(format_review(event)))
    except Exception:
        LOG.warning("VIDEO_OPPORTUNITY_SEND_FAILURE")
    status = "accepted" if accepted else "unconfirmed"
    try:
        with ledger.db:
            ledger.db.execute("""UPDATE video_opportunity_review_notice
                SET status=? WHERE bar_start=? AND direction=?""",
                (status, start, direction))
    except sqlite3.Error:
        LOG.warning("VIDEO_OPPORTUNITY_FINISH_FAILURE")
    return f"{event['lane']}:{status}"


def announce_release(ledger, *, revision, now, send, enabled):
    """Send one visible connectivity notice per revision, never a trade alert."""
    if not enabled or send is None:
        return "disabled"
    try:
        with ledger.db:
            ledger.db.execute("""CREATE TABLE IF NOT EXISTS
                video_opportunity_release(revision TEXT PRIMARY KEY,
                created_at REAL NOT NULL, status TEXT NOT NULL)""")
            if not ledger.db.execute("""INSERT OR IGNORE INTO video_opportunity_release
                VALUES(?,?,'reserved')""", (revision, now)).rowcount:
                return "already_reserved"
    except sqlite3.Error:
        return "ledger_unavailable"
    try:
        accepted = bool(send(
            "[BTCUSDT 구조기회 자동 감시 활성화]\n"
            "LBank 5분봉 21개부터 A/B/C 구조를 조기 감지합니다.\n"
            "상위 시간봉 검증 전에는 반드시 미확인 후보로 표시합니다.\n"
            "호가·비용·과거 가격 장벽 검사는 계속 적용됩니다.\n"
            "기준을 충족한 경우에만 별도 후보 알림을 보냅니다.\n"
            "정식 진입 승인은 실거래 검증으로 잠긴 상태입니다. 자동주문 없음.\n"
            "이 메시지는 통신·배포 확인용이며 진입 신호가 아닙니다."
        ))
    except Exception:
        accepted = False
    status = "accepted" if accepted else "unconfirmed"
    try:
        with ledger.db:
            ledger.db.execute(
                "UPDATE video_opportunity_release SET status=? WHERE revision=?",
                (status, revision))
    except sqlite3.Error:
        pass
    return status
