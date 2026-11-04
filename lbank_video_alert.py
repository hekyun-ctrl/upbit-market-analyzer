"""Standalone BTCUSDT video-strategy notifications; no ZB or order integration."""
from collections import Counter
from contextlib import closing
from datetime import datetime, timedelta, timezone
import logging
import math
import os
from pathlib import Path
import sqlite3
import sys
import time

from lbank_native_candles import NativeCandleThread
from lbank_telegram_transport import TelegramTransport
from research.zerobase.video_entry import Costs, WARMUP, evaluate_latest, judge_entry

LOG = logging.getLogger("lbank_video_alert")
SYMBOL = "BTCUSDT"
SOURCE = "lbank_native_1m_derived_5m"
MAX_BARS = 240
MAX_CLOSE_AGE = 30
MAX_BOOK_LATENCY = 5
MAX_SPREAD = .0005
STRATEGIES = {"rsi_reversal": "RSI 반전형", "bb_cci_breakout": "볼린저밴드·CCI 추세 지속형"}
KST = timezone(timedelta(hours=9))


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

    def claim(self, start, lane):
        with self.db:
            self.db.execute("DELETE FROM video_alert_delivery WHERE bar_start<?", (start-32*86400,))
            return self.db.execute("INSERT OR IGNORE INTO video_alert_delivery VALUES(?,?,'reserved')",
                                   (start, lane)).rowcount == 1

    def finish(self, start, status):
        with self.db:
            self.db.execute("UPDATE video_alert_delivery SET status=? WHERE bar_start=?", (status, start))


class PublicBook:
    def __init__(self, fetch=None):
        if fetch is None:
            import ccxt
            exchange = ccxt.lbank({"enableRateLimit": True, "timeout": 4500})
            fetch = exchange.contractPublicGetCfdOpenApiV1PubMarketOrder
        self.fetch = fetch

    def __call__(self):
        data = self.fetch({"symbol": SYMBOL, "depth": 20})["data"]
        if data.get("symbol") != SYMBOL:
            raise ValueError("wrong order book symbol")
        def prices(side):
            for level in data[side]:
                price, size = float(level["price"]), float(level["volume"])
                if not (math.isfinite(price) and math.isfinite(size) and price > 0 and size >= 0):
                    raise ValueError("invalid order book level")
                if size > 0:
                    yield price
        return max(prices("bids")), min(prices("asks"))


def format_alert(plan, price, rr, bid, ask, lanes):
    d = plan["direction"]
    close = datetime.fromtimestamp(plan["signal_start"]+300, KST).strftime("%m-%d %H:%M:%S KST")
    kind = "롱 상한" if d == 1 else "숏 하한"
    kinds = " / ".join(STRATEGIES[lane] for lane in sorted(lanes))
    return (f"[별도 영상 전략 알람 | 엘뱅크 BTCUSDT 5분봉]\n"
            f"{'롱' if d == 1 else '숏'} · {kinds} · {close} 마감\n"
            f"발송 전 {'매도' if d == 1 else '매수'} 1호가: {price:,.1f} USDT\n"
            f"허용 진입가({kind}): {plan['entry_bound']:,.1f}\n"
            f"목표 {plan['target']:,.1f} / 손절 {plan['stop']:,.1f} USDT\n"
            f"가정 비용 후 손익비 {rr:.2f} · 스프레드 {(ask-bid)/((ask+bid)/2):.3%}\n"
            "봉은 엘뱅크 1분 스트림에서 합산했습니다. 호가가 달라지면 다시 확인하세요.")


def process(bars, *, now, started, clock, get_book, ledger, send, costs=Costs()):
    """Process one newest candle; signal-close and live-book gates both apply."""
    if len(bars) < WARMUP:
        return "warmup", {}
    newest = bars[-1]
    start = newest["start"]
    end = start+300
    if end <= started or not end <= newest["received_at"] <= now <= end+MAX_CLOSE_AGE:
        return "stale_or_before_start", {}
    events, rejected = evaluate_latest(bars, costs=costs)
    counts = dict(Counter(event["status"] for event in events)) | rejected
    qualified = [event for event in events if event["status"] == "qualified"]
    if not qualified:
        return "no_qualified_entry", counts
    if len({event["direction"] for event in qualified}) != 1:
        return "conflicting_directions", counts
    try:
        bid, ask = get_book()
    except Exception as exc:
        LOG.warning("VIDEO_ALERT_BOOK_FAILURE type=%s", type(exc).__name__)
        return "book_unavailable", counts
    received = clock()
    if not now <= received <= min(now+MAX_BOOK_LATENCY, end+MAX_CLOSE_AGE):
        return "book_too_slow", counts
    if not all(math.isfinite(x) for x in (bid, ask)) or not 0 < bid <= ask:
        return "invalid_book", counts
    if (ask-bid)/((ask+bid)/2) > MAX_SPREAD:
        return "spread_too_wide", counts
    valid = []
    for event in qualified:
        d = event["direction"]
        price = ask if d == 1 else bid
        verdict, rr = judge_entry(event, price, costs)
        bound = event.get("entry_bound")
        if verdict != "qualified" or bound is None or d*(price-bound) > 0:
            key = "book_" + ("outside_rounded_entry_bound" if verdict == "qualified" else verdict)
            counts[key] = counts.get(key, 0)+1
            continue
        valid.append((rr, event, price))
    if not valid:
        return "book_rejected", counts
    rr, event, price = max(valid, key=lambda pair: pair[0])
    msg = format_alert(event, price, rr, bid, ask, {v[1]["lane"] for v in valid})
    if send is None:
        return "send_disabled", counts
    if not ledger.claim(start, event["lane"]):
        return "already_reserved", counts
    if not received <= clock() <= min(received+MAX_BOOK_LATENCY, end+MAX_CLOSE_AGE):
        ledger.finish(start, "stale_after_reservation")
        return "stale_after_reservation", counts
    sent = send(msg)
    status = "telegram_accepted" if sent else "telegram_unconfirmed_no_retry"
    ledger.finish(start, status)
    return status, counts


def run():
    started = time.time()
    data = Path(os.environ.get("VIDEO_ALERT_STATE_DIR", "/data")).resolve()
    enabled = os.getenv("VIDEO_ALERT_SEND_ENABLED", "0") == "1"
    volume = os.getenv("RAILWAY_VOLUME_MOUNT_PATH")
    if enabled and os.getenv("RAILWAY_SERVICE_ID") and (not volume or Path(volume).resolve() != data):
        raise RuntimeError("Refusing Telegram delivery without a dedicated persistent state mount")
    data.mkdir(parents=True, exist_ok=True)
    source_path = data / "video_alert_native.sqlite3"
    ledger = DeliveryLedger(data / "video_alert_delivery.sqlite3")
    telegram = TelegramTransport(token=os.getenv("VIDEO_ALERT_TELEGRAM_BOT_TOKEN", ""),
                                 chat_id=os.getenv("VIDEO_ALERT_TELEGRAM_CHAT_ID", ""))
    get_book = PublicBook()
    NativeCandleThread(path=str(source_path)).start()
    LOG.info("VIDEO_ALERT_START revision=%s source=%s send_enabled=%s warmup=%d separate=true",
             os.getenv("RAILWAY_GIT_COMMIT_SHA", "local"), SOURCE, enabled, WARMUP)
    if enabled:
        token_ok, chat_ok = telegram.health()
        LOG.info("VIDEO_ALERT_TELEGRAM_CHECK token_ok=%s chat_ok=%s no_test_message=true", token_ok, chat_ok)
    try:
        bid, ask = get_book()
        LOG.info("VIDEO_ALERT_BOOK_CHECK usable=%s", math.isfinite(bid) and math.isfinite(ask) and 0 < bid <= ask)
    except Exception as exc:
        LOG.warning("VIDEO_ALERT_BOOK_CHECK usable=false type=%s", type(exc).__name__)
    seen, next_health = None, 0
    while True:
        now = time.time()
        try:
            bars = read_window(source_path, now)
            if now >= next_health:
                LOG.info("VIDEO_ALERT_FEED ready=%s consecutive=%d required=%d latest=%s",
                         len(bars) >= WARMUP, len(bars), WARMUP, bars[-1]["start"] if bars else None)
                next_health = now+60
            if bars and bars[-1]["start"] != seen:
                seen = bars[-1]["start"]
                status, counts = process(bars, now=now, started=started, clock=time.time,
                                         get_book=get_book, ledger=ledger,
                                         send=telegram.send if enabled else None)
                LOG.info("VIDEO_ALERT_DECISION bar=%d status=%s reasons=%s", seen, status, counts)
        except (OSError, sqlite3.Error, ValueError, KeyError) as exc:
            LOG.warning("VIDEO_ALERT_POLL_FAILURE type=%s", type(exc).__name__)
        time.sleep(2)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, stream=sys.stdout)
    run()
