"""Standalone BTCUSDT video-strategy notifications; no ZB or order integration."""
from collections import Counter
from contextlib import closing
from dataclasses import asdict
from datetime import datetime, timedelta, timezone
import hashlib
import json
import logging
import math
import os
from pathlib import Path
import sqlite3
import sys
import time

from lbank_early_breakout_audit import persist_early_audit
from lbank_early_alert_delivery import activation_check, send_early_candidate, send_feed_warning
from lbank_scenario_alerts import send_scenario_candidate
from lbank_b_retest_research import persist_b_retest_shadow
from lbank_b_early_1m_shadow import read_closed_1m_window, persist_first_minute_shadow
from lbank_b_momentum_trial import evaluate_trial, evaluate_five_break, read_b_5m_context, deliver_trial
from lbank_native_book import NativeBookError, fetch_native_book
from lbank_native_candles import NativeCandleThread
from lbank_telegram_transport import TelegramTransport
from research.zerobase.rsi_sequence import POLICY as RSI_SEQUENCE_POLICY
from research.zerobase.video_entry import Costs, WARMUP, evaluate_latest, evaluate_rsi_shadow, judge_entry

LOG = logging.getLogger("lbank_video_alert")
SYMBOL = "BTCUSDT"
SOURCE = "lbank_native_1m_derived_5m"
MAX_BARS = 240
MAX_CLOSE_AGE = 30
MAX_BOOK_LATENCY = 5
MAX_SPREAD = .0005
STRATEGIES = {"rsi_reversal": "RSI 반전형", "bb_cci_breakout": "볼린저밴드·CCI 추세 지속형"}
KST = timezone(timedelta(hours=9))
LIVE_POLICY = "video_live_legacy_v1"
LIVE_SEQUENCE_POLICY = "video_live_" + RSI_SEQUENCE_POLICY
SHADOW_POLICY = RSI_SEQUENCE_POLICY + "_shadow"
RETENTION_SECONDS = 32 * 86400


def read_window(path, now):
    """Read only this service's recorder; do not create/repair its database."""
    expected = int(now // 300) * 300 - 300
    if not path.is_file():
        return []
    with closing(sqlite3.connect(path.resolve().as_uri()+"?mode=ro", uri=True,
                                 timeout=1)) as db:
        db.row_factory = sqlite3.Row
        rows = db.execute("""
            SELECT b.*, a.received AS received_at FROM native_bars b
            JOIN native_arrivals a ON b.period=a.period AND b.start=a.start
            WHERE b.period=300 AND b.quality='stream_closed' AND a.source=?
                AND b.start <= ? ORDER BY b.start DESC LIMIT ?
            """, (SOURCE, expected, MAX_BARS)).fetchall()
    result = []
    for row in rows:
        if (row["start"] != expected or not expected + 300 <= row["received_at"] <= now
                or row["received_at"] - (expected + 300) > MAX_CLOSE_AGE):
            break
        result.append(dict(row))
        expected -= 300
    return result[::-1]


class DeliveryLedger:
    """Reserve a candle before Telegram so an ambiguous timeout isn't retried."""
    def __init__(self, path):
        self.db = sqlite3.connect(path, timeout=2)
        with self.db:
            self.db.execute("""CREATE TABLE IF NOT EXISTS video_alert_delivery
                (bar_start INTEGER PRIMARY KEY, lane TEXT, status TEXT)""")
            self.db.execute("""CREATE TABLE IF NOT EXISTS video_alert_patterns
                (setup_id TEXT PRIMARY KEY, bar_start INTEGER NOT NULL)""")
            self.db.execute("""CREATE INDEX IF NOT EXISTS video_alert_patterns_bar
                ON video_alert_patterns(bar_start)""")
            self.db.execute("""CREATE TABLE IF NOT EXISTS video_alert_decisions
                (bar_start INTEGER, policy TEXT, evaluated_at REAL, status TEXT,
                 evidence TEXT NOT NULL, PRIMARY KEY (bar_start, policy))""")

    def record_decision(self, start, policy, evaluated_at, status, evidence):
        payload = json.dumps(evidence, sort_keys=True, ensure_ascii=False, allow_nan=False)
        with self.db:
            self.db.execute("DELETE FROM video_alert_decisions WHERE bar_start<?",
                            (start-RETENTION_SECONDS,))
            self.db.execute("INSERT OR IGNORE INTO video_alert_decisions VALUES(?,?,?,?,?)",
                            (start, policy, evaluated_at, status, payload))

    def claim(self, start, lane, setup_id=None):
        with self.db:
            # Acquire the write transaction before checking either key, so two
            # workers cannot both reserve the same setup on different candles.
            cutoff = start-RETENTION_SECONDS
            self.db.execute("DELETE FROM video_alert_delivery WHERE bar_start<?", (cutoff,))
            self.db.execute("DELETE FROM video_alert_patterns WHERE bar_start<?", (cutoff,))
            if setup_id and self.db.execute("SELECT 1 FROM video_alert_patterns WHERE setup_id=?",
                                           (setup_id,)).fetchone():
                return False
            claimed = self.db.execute("INSERT OR IGNORE INTO video_alert_delivery VALUES(?,?,'reserved')",
                                      (start, lane)).rowcount == 1
            if claimed and setup_id:
                self.db.execute("INSERT INTO video_alert_patterns VALUES(?,?)", (setup_id, start))
            return claimed

    def finish(self, start, status):
        with self.db:
            self.db.execute("UPDATE video_alert_delivery SET status=? WHERE bar_start=?", (status, start))


class BookDataError(ValueError):
    """A fixed diagnostic code, safe to write without the response body."""


class PublicBook:
    DEPTHS = (20, 10, 50, 5, 60, 1)
    MAX_ATTEMPTS = 4
    BUDGET = 4.0

    def __init__(self, fetch=None, *, alternate=None, clock=time.monotonic):
        self.exchange = None
        if fetch is None:
            import ccxt
            self.exchange = ccxt.lbank({"enableRateLimit": True, "timeout": 1000})
            fetch = self.exchange.contractPublicGetCfdOpenApiV1PubMarketOrder
        self.fetch = fetch
        self.alternate = alternate
        self.clock = clock
        self.depth = self.DEPTHS[0]
        self.last_source = None

    @staticmethod
    def parse(response):
        data = response.get("data") if isinstance(response, dict) else None
        if not isinstance(data, dict):
            raise BookDataError("response_missing_data")
        symbol = data.get("symbol")
        if not isinstance(symbol, str) or symbol.strip().upper() != SYMBOL:
            raise BookDataError("response_symbol_mismatch" if symbol else "response_missing_symbol")

        def prices(side):
            raw = data.get(side)
            if not isinstance(raw, list):
                raise BookDataError("response_missing_"+side)
            result = []
            for level in raw:
                try:
                    size = float(level["volume"])
                    price = float(level["price"])
                except (KeyError, TypeError, ValueError, OverflowError):
                    raise BookDataError("invalid_level_fields") from None
                if not (math.isfinite(size) and size >= 0):
                    raise BookDataError("invalid_level_size")
                if size == 0:
                    continue  # The API includes zero-volume placeholders.
                if not (math.isfinite(price) and price > 0):
                    raise BookDataError("invalid_level_price")
                result.append(price)
            if not result:
                raise BookDataError("no_live_"+side)
            return result

        bid, ask = max(prices("bids")), min(prices("asks"))
        if bid >= ask:
            raise BookDataError("locked_or_crossed_book")
        return bid, ask

    def __call__(self):
        """Retry only same-market public snapshots; never carry an old quote."""
        end = self.clock() + self.BUDGET
        i = self.DEPTHS.index(self.depth)
        depths = self.DEPTHS[i:] + self.DEPTHS[:i]
        tried = []
        self.last_source = None
        if self.alternate is not None:
            try:
                bid, ask = self.alternate(min(2.0, end - self.clock()))
                if not all(math.isfinite(x) for x in (bid, ask)) or not 0 < bid < ask:
                    raise BookDataError("invalid_book")
                if self.clock() > end:
                    raise BookDataError("deadline")
            except Exception as exc:
                tried.append("ws:" + book_error_code(exc))
            else:
                self.last_source = "websocket"
                return bid, ask
        for depth in depths[:self.MAX_ATTEMPTS]:
            remaining = end - self.clock()
            if remaining < .12:
                break
            if self.exchange is not None:
                self.exchange.timeout = max(1, int(1000 * min(1., remaining)))
            try:
                quote = self.parse(self.fetch({"symbol": SYMBOL, "depth": depth}))
            except Exception as exc:
                tried.append(f"{depth}:{book_error_code(exc)}")
                continue
            if self.clock() > end:
                tried.append(f"{depth}:deadline")
                break
            self.depth = depth
            self.last_source = "rest"
            if tried:
                LOG.info("VIDEO_ALERT_BOOK_RECOVERED depth=%s earlier=%s", depth, ",".join(tried))
            return quote
        self.depth = self.DEPTHS[(i+max(1, len(tried) - int(self.alternate is not None))) % len(self.DEPTHS)]
        raise BookDataError("snapshot_attempts=" + (",".join(tried) or "deadline"))


def book_error_code(exc):
    return str(exc) if isinstance(exc, (BookDataError, NativeBookError)) else type(exc).__name__


def check_book(get_book):
    """Exercise the same live quote path at startup without sending."""
    try:
        get_book()
    except Exception as exc:
        LOG.warning("VIDEO_ALERT_BOOK_CHECK usable=false reason=%s", book_error_code(exc))
    else:
        LOG.info("VIDEO_ALERT_BOOK_CHECK usable=true source=%s depth=%s", get_book.last_source,
                 get_book.depth if get_book.last_source == "rest" else "native")


def format_alert(plan, price, rr, bid, ask, lanes):
    d = plan["direction"]
    close = datetime.fromtimestamp(plan["signal_start"]+300, KST).strftime("%m-%d %H:%M:%S KST")
    kind = "롱 상한" if d == 1 else "숏 하한"
    kinds = " / ".join(STRATEGIES[lane] for lane in sorted(lanes))
    def bar_time(start, *, closed=False):
        return datetime.fromtimestamp(start+300*closed, KST).strftime("%m-%d %H:%M")

    evidence = ""
    if plan.get("policy") == RSI_SEQUENCE_POLICY:
        first = bar_time(plan["first_confirmation_start"], closed=True)
        second = bar_time(plan["second_confirmation_start"], closed=True)
        extreme = bar_time(plan["second_start"])
        evidence = ("[우주심 직접 테스트용 RSI · 수익성 미검증]\n"
                    f"첫 반전 마감 {first} → 두 번째 반전 마감 {second} (KST)\n"
                    f"두 번째 극점 {extreme} 시작봉 · {plan['second_price']:,.1f} USDT\n")
    elif plan["lane"] == "rsi_reversal":
        evidence = (f"첫 극점 {bar_time(plan['first_start'])} 시작봉 (KST)\n"
                    f"두 번째 극점 {bar_time(plan['second_start'])} 시작봉"
                    f" · {plan['anchor']:,.1f} USDT\n")
    target_basis = {"confirmed_pivot": "확정 지지·저항 앞",
                    "signal_bar_bb_center": "신호봉 볼린저 중심선 앞"}
    return (f"[별도 영상 전략 알람 | 엘뱅크 BTCUSDT 5분봉]\n"
            f"{'롱' if d == 1 else '숏'} · {kinds} · {close} 마감\n"
            f"{evidence}"
            f"발송 전 {'매도' if d == 1 else '매수'} 1호가: {price:,.1f} USDT\n"
            f"허용 진입가({kind}): {plan['entry_bound']:,.1f}\n"
            f"목표 {plan['target']:,.1f} / 손절 {plan['stop']:,.1f} USDT\n"
            f"목표 근거: {target_basis.get(plan.get('target_basis'), '구조 목표')}\n"
            f"가정 비용 후 손익비 {rr:.2f} · 스프레드 {(ask-bid)/((ask+bid)/2):.3%}\n"
            "봉은 엘뱅크 1분 스트림에서 합산했습니다. 호가가 달라지면 다시 확인하세요.")


def _read_quote(get_book, now, end, clock):
    book = {"requested_at": now}
    try:
        bid, ask = get_book()
    except Exception as exc:
        reason = book_error_code(exc)
        LOG.warning("VIDEO_ALERT_BOOK_FAILURE reason=%s", reason)
        return dict(book, status="book_unavailable", reason=reason)
    received = clock()
    source = getattr(get_book, "last_source", None)
    book.update(received_at=received, source=source if source in ("websocket", "rest") else "unspecified")
    valid = all(type(x) in (float, int) and math.isfinite(x) for x in (bid, ask))
    if valid:
        book.update(bid=bid, ask=ask)
    if not now <= received <= min(now+MAX_BOOK_LATENCY, end+MAX_CLOSE_AGE):
        return dict(book, status="book_too_slow")
    if not valid or not 0 < bid < ask:
        return dict(book, status="invalid_book")
    spread = (ask-bid)/((ask+bid)/2)
    return dict(book, spread=spread, status="spread_too_wide" if spread > MAX_SPREAD else "usable")


def _screen_quote(qualified, book, costs, counts):
    valid, checks = [], []
    for event in qualified:
        d = event["direction"]
        price = book["ask"] if d == 1 else book["bid"]
        verdict, rr = judge_entry(event, price, costs)
        bound = event.get("entry_bound")
        status = verdict
        if verdict != "qualified" or bound is None or d*(price-bound) > 0:
            status = "outside_rounded_entry_bound" if verdict == "qualified" else verdict
            key = "book_" + status
            counts[key] = counts.get(key, 0)+1
        else:
            valid.append((rr, event, price))
        checks.append(dict(lane=event["lane"], direction=d, quote_price=price, net_rr=rr, status=status))
    return valid, checks


def _evidence(bars, costs):
    payload = json.dumps(bars, sort_keys=True, separators=(",", ":"), allow_nan=False)
    return dict(costs=asdict(costs), input_sha256=hashlib.sha256(payload.encode()).hexdigest(),
                events=[], reasons={}, book={"status": "not_requested"})


def _record(ledger, start, policy, now, status, evidence):
    try:
        ledger.record_decision(start, policy, now, status, evidence)
        return True
    except (sqlite3.Error, OSError, ValueError, TypeError) as exc:
        # Delivery reservations stay mandatory. The diagnostic write cannot
        # erase a Telegram acceptance or turn a shadow candidate into a send.
        LOG.warning("VIDEO_ALERT_EVIDENCE_FAILURE policy=%s type=%s", policy, type(exc).__name__)
        return False


def _legacy_setup_id(event):
    if event["lane"] == "rsi_reversal":
        if event.get("policy") == RSI_SEQUENCE_POLICY:
            return event["setup_id"]
        return f"rsi_legacy:{event['direction']}:{event['first_start']}"
    return None


def _finish(ledger, start, status):
    try:
        ledger.finish(start, status)
    except (sqlite3.Error, OSError) as exc:
        # The committed reservation still prevents retry. Preserve the actual
        # transport outcome in the decision log, even if its update fails.
        LOG.warning("VIDEO_ALERT_DELIVERY_STATUS_FAILURE type=%s", type(exc).__name__)


def _live_decision(bars, *, now, end, clock, get_book, ledger, send, costs, evidence,
                   rsi_sequence=False):
    start = bars[-1]["start"]
    events, rejected = evaluate_latest(bars, costs=costs, rsi_sequence=rsi_sequence)
    evidence["events"] = events
    counts = dict(Counter(event["status"] for event in events) + Counter(rejected))
    qualified = [event for event in events if event["status"] == "qualified"]
    if not qualified:
        return "no_qualified_entry", counts
    if len({event["direction"] for event in qualified}) != 1:
        return "conflicting_directions", counts
    book = evidence["book"] = _read_quote(get_book, now, end, clock)
    if book["status"] != "usable":
        return book["status"], counts
    valid, evidence["book_checks"] = _screen_quote(qualified, book, costs, counts)
    if not valid:
        return "book_rejected", counts
    # A repeat RSI setup must not suppress a separate eligible CCI setup.
    for rr, event, price in sorted(valid, key=lambda pair: pair[0], reverse=True):
        setup_id = _legacy_setup_id(event)
        msg = format_alert(event, price, rr, book["bid"], book["ask"], {event["lane"]})
        evidence["selected"] = dict(lane=event["lane"], direction=event["direction"],
                                    quote_price=price, net_rr=rr, setup_id=setup_id)
        if send is None:
            return "send_disabled", counts
        try:
            if ledger.claim(start, event["lane"], setup_id):
                break
        except (sqlite3.Error, OSError) as exc:
            evidence["reservation_error"] = type(exc).__name__
            return "delivery_reservation_failed", counts
    else:
        evidence.pop("selected", None)
        return "already_reserved", counts
    received = book["received_at"]
    if not received <= clock() <= min(received+MAX_BOOK_LATENCY, end+MAX_CLOSE_AGE):
        _finish(ledger, start, "stale_after_reservation")
        return "stale_after_reservation", counts
    sent = send(msg)
    status = "telegram_accepted" if sent else "telegram_unconfirmed_no_retry"
    _finish(ledger, start, status)
    return status, counts


def _shadow_decision(bars, *, now, end, clock, get_book, costs, live_book, evidence):
    events, rejected, states = evaluate_rsi_shadow(bars, costs=costs)
    evidence.update(events=events, states=states, reasons=dict(Counter(e["status"] for e in events)
                                                             + Counter(rejected)))
    qualified = [event for event in events if event["status"] == "qualified"]
    if not qualified:
        return "no_qualified_entry"
    if len({event["direction"] for event in qualified}) != 1:
        return "conflicting_directions"
    if live_book["status"] != "not_requested":
        book = dict(live_book, reused_live_evaluation=True)
    elif now <= clock() <= min(now+MAX_BOOK_LATENCY, end+MAX_CLOSE_AGE):
        book = _read_quote(get_book, now, end, clock)
    else:
        book = {"status": "shadow_quote_window_elapsed"}
    evidence["book"] = book
    if book["status"] != "usable":
        return book["status"]
    valid, evidence["book_checks"] = _screen_quote(qualified, book, costs, evidence["reasons"])
    if not valid:
        return "book_rejected"
    rr, event, price = max(valid, key=lambda pair: pair[0])
    evidence["alert_preview"] = ("관찰 전용 · 미발송\n" +
                                 format_alert(event, price, rr, book["bid"], book["ask"], {event["lane"]}))
    return "shadow_eligible"


def process(bars, *, now, started, clock, get_book, ledger, send, costs=Costs(), shadow=False,
            rsi_sequence=False):
    """Evaluate one completed bar; sequence mode is enabled explicitly by run()."""
    if not bars:
        return "warmup", {}
    newest = bars[-1]
    start = newest["start"]
    end = start+300
    if len(bars) < WARMUP:
        return "warmup", {}
    if end <= started or not end <= newest["received_at"] <= now <= end+MAX_CLOSE_AGE:
        return "stale_or_before_start", {}
    live = {"events": [], "book": {"status": "not_requested"}}
    status, counts = _live_decision(bars, now=now, end=end, clock=clock, get_book=get_book,
                                    ledger=ledger, send=send, costs=costs, evidence=live,
                                    rsi_sequence=rsi_sequence)
    # Evidence and research run after the live decision, so they cannot spend
    # its quote or delivery budget.
    try:
        base = _evidence(bars, costs)
        policy = LIVE_SEQUENCE_POLICY if rsi_sequence else LIVE_POLICY
        live_saved = _record(ledger, start, policy, now, status, dict(base, **live, reasons=counts))
        if rsi_sequence:
            LOG.info("VIDEO_ALERT_LEDGER bar=%d policy=%s stored=%s", start, policy, live_saved)
        if shadow:
            observed = dict(base, delivery="shadow_only")
            result = _shadow_decision(bars, now=now, end=end, clock=clock, get_book=get_book,
                                      costs=costs, live_book=live["book"], evidence=observed)
            shadow_saved = _record(ledger, start, SHADOW_POLICY, now, result, observed)
            LOG.info("VIDEO_ALERT_SHADOW bar=%d status=%s live_ledger_ok=%s shadow_ledger_ok=%s reasons=%s",
                     start, result, live_saved, shadow_saved, observed["reasons"])
    except (sqlite3.Error, OSError, ValueError, TypeError, KeyError, ArithmeticError) as exc:
        LOG.warning("VIDEO_ALERT_EVIDENCE_FAILURE bar=%d type=%s", start, type(exc).__name__)
    return status, counts


def run():
    started = time.time()
    data = Path(os.environ.get("VIDEO_ALERT_STATE_DIR", "/data")).resolve()
    enabled = os.getenv("VIDEO_ALERT_SEND_ENABLED", "0") == "1"
    early_enabled = enabled and os.getenv("VIDEO_EARLY_ALERT_SEND_ENABLED", "0") == "1"
    scenario_enabled = enabled and os.getenv("VIDEO_SCENARIO_ALERT_SEND_ENABLED", "0") == "1"
    momentum_trial_enabled = enabled and os.getenv("VIDEO_B_MOMENTUM_TEST_SEND_ENABLED", "0") == "1"
    system_enabled = enabled and os.getenv("VIDEO_ALERT_SYSTEM_NOTICES_ENABLED", "0") == "1"
    volume = os.getenv("RAILWAY_VOLUME_MOUNT_PATH")
    if enabled and os.getenv("RAILWAY_SERVICE_ID") and (not volume or Path(volume).resolve() != data):
        raise RuntimeError("Refusing Telegram delivery without a dedicated persistent state mount")
    data.mkdir(parents=True, exist_ok=True)
    source_path = data / "video_alert_native.sqlite3"
    ledger = DeliveryLedger(data / "video_alert_delivery.sqlite3")
    telegram = TelegramTransport(token=os.getenv("VIDEO_ALERT_TELEGRAM_BOT_TOKEN", ""),
                                 chat_id=os.getenv("VIDEO_ALERT_TELEGRAM_CHAT_ID", ""))
    get_book = PublicBook(alternate=fetch_native_book)
    NativeCandleThread(path=str(source_path)).start()
    LOG.info("VIDEO_ALERT_START revision=%s source=%s send_enabled=%s warmup=%d separate=true",
             os.getenv("RAILWAY_GIT_COMMIT_SHA", "local"), SOURCE, enabled, WARMUP)
    LOG.info("VIDEO_ALERT_POLICIES live=%s shadow=disabled experimental_rsi=true", LIVE_SEQUENCE_POLICY)
    LOG.info("VIDEO_SCENARIO_CONFIG enabled=%s events_only=true warmup=21", scenario_enabled)
    LOG.info("VIDEO_B_MOMENTUM_TEST_CONFIG enabled=%s test_only=true no_orders=true", momentum_trial_enabled)
    if enabled:
        token_ok, chat_ok = telegram.health()
        LOG.info("VIDEO_ALERT_TELEGRAM_CHECK token_ok=%s chat_ok=%s no_test_message=true", token_ok, chat_ok)
    check_book(get_book)
    revision = os.getenv("RAILWAY_GIT_COMMIT_SHA", "local")
    activation = activation_check(ledger, now=time.time(), revision=revision,
                                  send=telegram.send if enabled else None,
                                  enabled=system_enabled and early_enabled,
                                  scenario_enabled=scenario_enabled)
    LOG.info("VIDEO_ACTIVATION_CHECK status=%s early_enabled=%s system_enabled=%s",
             activation, early_enabled, system_enabled)
    seen, next_health = None, 0
    minute_seen = None
    last_fresh = started
    while True:
        now = time.time()
        try:
            bars = read_window(source_path, now)
            # B TEST only: preserve real older receipts across <=2 missing
            # 5m slots after deploy. Formal read_window stays fail-closed.
            b_context = read_b_5m_context(source_path, now)
            if now >= next_health:
                LOG.info("VIDEO_ALERT_FEED ready=%s consecutive=%d required=%d latest=%s",
                         len(bars) >= WARMUP, len(bars), WARMUP, bars[-1]["start"] if bars else None)
                next_health = now+60
            if bars and bars[-1]["start"] != seen:
                seen = bars[-1]["start"]
                status, counts = process(bars, now=now, started=started, clock=time.time,
                                         get_book=get_book, ledger=ledger,
                                         send=telegram.send if enabled else None, rsi_sequence=True)
                LOG.info("VIDEO_ALERT_DECISION bar=%d status=%s reasons=%s", seen, status, counts)
                # A research-only observation, never an alert or entry override.
                # Journal failure cannot interrupt Telegram delivery or live evaluation.
                try:
                    observed = persist_early_audit(
                        ledger, bars, now=now, final_status=status, final_reasons=counts)
                except (sqlite3.Error, OSError, ValueError, TypeError, KeyError) as exc:
                    LOG.warning("VIDEO_EARLY_AUDIT_FAILURE type=%s", type(exc).__name__)
                else:
                    LOG.info(
                        "VIDEO_EARLY_AUDIT bar=%d status=%s direction=%s volume_multiple=%s final_status=%s",
                        seen, observed["status"], observed.get("direction"),
                        observed.get("volume_multiple"), status)
                # Research-only independent failed-reclaim B-short path.
                # Never overrides the existing qualified entry decision.
                try:
                    b_shadow = persist_b_retest_shadow(
                        ledger, bars, now=now, started=started)
                except (sqlite3.Error, OSError, ValueError, TypeError,
                        KeyError, ArithmeticError) as exc:
                    LOG.warning("VIDEO_B_RETEST_SHADOW_ERROR type=%s", type(exc).__name__)
                else:
                    LOG.info("VIDEO_B_RETEST_SHADOW bar=%d status=%s trend=%s rank=%s rr=%s",
                             seen, b_shadow["status"], b_shadow.get("trend", "unknown"),
                             (b_shadow.get("priorities") or ["관망"])[0],
                             b_shadow.get("net_rr_at_close"))
                if (len(bars) >= WARMUP and
                        bars[-1]["start"] + 300 <= now <= bars[-1]["start"] + 300 + MAX_CLOSE_AGE):
                    last_fresh = now
                # Evaluate the A/B/C observational lane before the generic
                # early-breakout lane, to avoid duplicate low-confidence alerts.
                # Never change the qualified RSI/CCI entry decision.
                try:
                    scenario_status = send_scenario_candidate(
                        ledger, bars, now=now, started=started, final_status=status,
                        send=telegram.send if enabled else None,
                        enabled=scenario_enabled)
                except (sqlite3.Error, OSError, ValueError, KeyError, TypeError) as exc:
                    LOG.warning("VIDEO_SCENARIO_FAILURE type=%s", type(exc).__name__)
                    scenario_status = "evaluation_failed"
                LOG.info("VIDEO_SCENARIO_DELIVERY bar=%d status=%s", seen, scenario_status)
                observed = scenario_status.split(":")[-1] in ("accepted", "unconfirmed")
                early_status = send_early_candidate(
                    ledger, bars, now=now, final_status=status,
                    send=telegram.send if enabled else None,
                    enabled=early_enabled and not observed)
                LOG.info("VIDEO_EARLY_DELIVERY bar=%d status=%s", seen, early_status)
                # NEW: dedicated RSI-independent first-support-break B test.
                # Cost/quote checked; explicitly unverified, never an order
                # and never an override of legacy formal signal approval.
                try:
                    five_trial = evaluate_five_break(b_context, allow_small_gaps=True)
                    five_trial_delivery = deliver_trial(
                        ledger, five_trial, now=now, get_book=get_book,
                        clock=time.time,
                        send=telegram.send if enabled else None,
                        enabled=momentum_trial_enabled, started=started)
                    LOG.info(
                        "VIDEO_B_FIVE_TEST_DECISION bar=%d candidate=%s "
                        "delivery=%s trend=%s rr=%s available=%d required=%d",
                        seen, five_trial["status"], five_trial_delivery,
                        five_trial.get("trend", "unknown"),
                        five_trial.get("net_rr"), len(b_context), 72)
                except (sqlite3.Error, OSError, ValueError, TypeError,
                        KeyError, ArithmeticError) as exc:
                    LOG.warning("VIDEO_B_FIVE_TEST_FAILURE type=%s",
                                type(exc).__name__)
            # Observe the FIRST completed 1m B-support break, without
            # waiting for 1-3 future 5m confirmations. Diagnostics only.
            # Absolutely no Telegram or order calls in this path.
            try:
                expected_minute = int(now//60)*60-60
                if expected_minute != minute_seen:
                    minutes = read_closed_1m_window(source_path, now)
                    if minutes and minutes[-1]["start"] == expected_minute:
                        minute_seen = expected_minute
                        first = persist_first_minute_shadow(
                            ledger, minutes, bars, now=now, started=started)
                        LOG.info(
                            "VIDEO_B_FIRST_MINUTE_SHADOW minute=%d status=%s trend=%s "
                            "priority=%s support=%s observed_close=%s rr=%s",
                            minute_seen, first["status"],
                            first.get("trend", "unknown"),
                            first.get("priority", "관망"),
                            first.get("broken_support"),
                            first.get("close"), first.get("net_rr"))
                        # Independent cost-checked 1m test-only entry candidate.
                        # A projected 2R exit is NOT a known market support:
                        # never describe this lane as historically verified.
                        # Disabled by default; no broker/account/order calls.
                        trial = evaluate_trial(minutes, b_context, allow_small_gaps=True)
                        trial_status = deliver_trial(
                            ledger, trial, now=now, get_book=get_book,
                            clock=time.time,
                            send=telegram.send if enabled else None,
                            enabled=momentum_trial_enabled, started=started)
                        LOG.info(
                            "VIDEO_B_MOMENTUM_TEST_DECISION minute=%d "
                            "candidate=%s delivery=%s trend=%s rr=%s",
                            minute_seen, trial["status"], trial_status,
                            trial.get("trend","unknown"),trial.get("net_rr"))
            except (OSError, sqlite3.Error, ValueError, TypeError,
                    KeyError, ArithmeticError) as exc:
                LOG.warning("VIDEO_B_FIRST_MINUTE_ERROR type=%s",
                            type(exc).__name__)
            warning = send_feed_warning(
                ledger, now=now, last_fresh=last_fresh,
                send=telegram.send if enabled else None, enabled=system_enabled,
                consecutive=len(bars), required=WARMUP)
            if warning not in ("healthy_or_grace", "disabled", "already_reserved"):
                LOG.warning("VIDEO_FEED_WARNING status=%s last_fresh_at=%s", warning, last_fresh)
        except (OSError, sqlite3.Error, ValueError, KeyError) as exc:
            LOG.warning("VIDEO_ALERT_POLL_FAILURE type=%s", type(exc).__name__)
        time.sleep(2)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, stream=sys.stdout)
    run()
