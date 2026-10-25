"""Bounded, public-data ZIL volume watch; observations only, never orders."""
from __future__ import annotations

import asyncio
import logging
import os
import time
from datetime import datetime, timezone, timedelta
from statistics import median

from upbit_client import UpbitPublicClient

LOGGER = logging.getLogger(__name__)
MARKET = "KRW-ZIL"
KST = timezone(timedelta(hours=9))
# Known Upbit termination time. May be overridden only after checking a new notice.
END_DEFAULT = "2026-11-16T15:00:00+09:00"
STATUS = {"state": "not_started", "interval_seconds": 60, "market": MARKET}


def end_at():
    value = datetime.fromisoformat(os.getenv("ZIL_WATCH_END_AT", END_DEFAULT))
    if value.tzinfo is None:
        raise ValueError("ZIL_WATCH_END_AT must include timezone")
    return value.timestamp()


def enabled():
    return os.getenv("ZIL_WATCH_ENABLED", "true").lower() in {"1", "true", "yes"}


def stamp(row):
    return datetime.fromisoformat(row["candle_date_time_utc"]).replace(tzinfo=timezone.utc).timestamp()


def completed(rows, minutes, now):
    return sorted({stamp(r): r for r in rows if stamp(r) + minutes * 60 <= now}.values(), key=stamp)


def detect(rows, minutes, now):
    """Scan recent closes with strictly prior baselines, never unfinished candles."""
    bars = completed(rows, minutes, now)
    hits = []
    threshold, minimum = (5, 10_000_000) if minutes == 1 else (3, 30_000_000)
    for i in range(20, len(bars)):
        row = bars[i]
        start = stamp(row)
        if not now - 180 < start + minutes * 60 <= now:
            continue  # Bounded recovery, not a replay of old alarms.
        prior = bars[i-20:i]
        baseline = median(float(r["candle_acc_trade_volume"]) for r in prior)
        value = float(row["candle_acc_trade_price"])
        ratio = float(row["candle_acc_trade_volume"]) / baseline if baseline > 0 else None
        if ratio is None or ratio < threshold or value < minimum:
            continue
        hits.append({"id": f"{MARKET}:{minutes}:{int(start)}", "start": start,
                     "minutes": minutes, "ratio": ratio, "value": value,
                     "change": (float(row["trade_price"]) / float(row["opening_price"]) - 1) * 100,
                     "close": float(row["trade_price"]), "high": float(row["high_price"]),
                     "low": float(row["low_price"]),
                     "sparse": start - stamp(prior[0]) > 25 * minutes * 60,
                     "prior_high": max(float(r["high_price"]) for r in prior),
                     "prior_low": min(float(r["low_price"]) for r in prior)})
    return hits


def trend(rows, minutes, now):
    bars = completed(rows, minutes, now)
    if len(bars) < 3:
        return "자료 부족"
    closes = [float(r["trade_price"]) for r in bars[-3:]]
    return "상승" if closes[0] < closes[1] < closes[2] else "하락" if closes[0] > closes[1] > closes[2] else "혼조"


def message(hits, ticker, five, hour, book, now):
    price = float(ticker["trade_price"])
    bars = completed(five, 5, now)[-36:]
    lows = [float(r["low_price"]) for r in bars if float(r["low_price"]) < price]
    highs = [float(r["high_price"]) for r in bars if float(r["high_price"]) > price]
    support = f"{max(lows):g}원" if lows else "확인 불가"
    resistance = f"{min(highs):g}원" if highs else "확인 불가"
    hit = max(hits, key=lambda h: h["start"] + h["minutes"] * 60)
    direction = "상승 동반" if hit["change"] > 0 else "하락 동반" if hit["change"] < 0 else "방향 불분명"
    flow = "기존 고점 돌파 유지" if hit["close"] > hit["prior_high"] and price > hit["prior_high"] else "돌파 후 되돌림" if hit["high"] > hit["prior_high"] else "기존 저점 이탈" if price < hit["prior_low"] else "박스권 내 급증"
    units = book.get("orderbook_units", []) if book else []
    spread = ((float(units[0]["ask_price"]) / float(units[0]["bid_price"]) - 1) * 100) if units else None
    spread_text = f"{spread:.2f}%" if spread is not None else "확인 불가"
    details = " / ".join(f"{datetime.fromtimestamp(h['start'], KST):%H:%M} {h['minutes']}분 {h['ratio']:.1f}배·{h['value']/10000:,.0f}만원" + ("(저유동성 기준)" if h["sparse"] else "") for h in hits[-3:])
    return (f"[질리카 전용 관찰 | {direction}]\n"
            f"확인 {datetime.fromtimestamp(now, KST):%m/%d %H:%M:%S} KST · 현재 {price:g}원\n"
            f"발생 {details} · 기준은 각각 직전 20개 완료봉 중앙값\n"
            f"최근 급증봉 {hit['change']:+.2f}% · {flow} · 1시간 흐름 {trend(hour, 60, now)}\n"
            f"가까운 관측 지지 {support} / 저항 {resistance} · 호가 간격 {spread_text}\n"
            "완료봉 급증 관찰이며 매수 신호가 아닙니다. 추격 시 되돌림·체결 위험이 있습니다.")


class ZilWatcher:
    def __init__(self, dispatcher, audit, client):
        self.dispatcher, self.audit, self.client = dispatcher, audit, client

    async def tick(self, now=None):
        fixed_now = now is not None
        now = time.time() if now is None else now
        if now >= end_at():
            STATUS.update(state="expired", checked_at=now)
            return False
        one = await self.client.candles(MARKET, "minute1", 200)
        five = await self.client.candles(MARKET, "minute5", 100)
        checked = now if fixed_now else time.time()
        if checked >= end_at():
            STATUS.update(state="expired", checked_at=checked)
            return False
        hits = detect(one, 1, checked) + detect(five, 5, checked)
        seen = self.audit.get("zil_watch_sent") or {}
        hits = [h for h in hits if h["id"] not in seen]
        STATUS.update(state="running", checked_at=checked, new_events=len(hits),
                      latest_1m_start=max((stamp(r) for r in one), default=None))
        LOGGER.info("ZIL_WATCH_TICK interval=60 events=%s latest_1m=%s", len(hits), STATUS["latest_1m_start"])
        if not hits:
            return True
        ticker = await self.client.ticker(MARKET)
        hour = await self.client.candles(MARKET, "minute60", 30)
        book = await self.client.orderbook(MARKET)
        if abs(checked - float(ticker["timestamp"]) / 1000) > 120:
            raise ValueError("stale ZIL ticker; delivery deferred")
        if self.dispatcher.mode != "telegram":
            raise RuntimeError("ZIL Telegram delivery unavailable")
        await self.dispatcher._deliver_telegram(message(hits, ticker, five, hour, book, checked),
                                                category="zil_volume_watch", market=MARKET,
                                                signal=",".join(h["id"] for h in hits))
        # Only acknowledged sends are suppressed. Network ambiguity can still duplicate.
        seen = {key: value for key, value in seen.items() if value > checked - 86400}
        seen.update({h["id"]: checked for h in hits})
        self.audit.set("zil_watch_sent", seen)
        LOGGER.info("ZIL_WATCH_DELIVERED events=%s", len(hits))
        return True


async def run_zil_watch(dispatcher, audit):
    if not enabled():
        STATUS["state"] = "disabled"
        return
    if audit is None:
        STATUS["state"] = "blocked_no_persistent_audit"
        LOGGER.error("ZIL_WATCH_BLOCKED persistent audit required")
        return
    client = UpbitPublicClient()
    watcher = ZilWatcher(dispatcher, audit, client)
    LOGGER.info("ZIL_WATCH_STARTED interval=60 market=%s end=%s mode=%s", MARKET, end_at(), dispatcher.mode)
    try:
        while enabled():
            try:
                if not await watcher.tick():
                    LOGGER.info("ZIL_WATCH_EXPIRED")
                    return
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                STATUS.update(state="error", error_type=type(exc).__name__)
                LOGGER.error("ZIL_WATCH_ERROR type=%s", type(exc).__name__)
            # Minute boundary + 3s lets REST publish the completed candle.
            await asyncio.sleep(60 - time.time() % 60 + 3)
    finally:
        await client.close()
