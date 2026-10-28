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
INTERVAL_SECONDS = 300
STATUS = {"state": "not_started", "interval_seconds": INTERVAL_SECONDS, "market": MARKET}
POLICY_VERSION = "five-minute-volume-peak-context-v4"
THRESHOLDS = {5: {"ratio": 3, "min_value_krw": 30_000_000}}


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
    if minutes not in THRESHOLDS:
        return []
    bars = completed(rows, minutes, now)
    hits = []
    threshold, minimum = THRESHOLDS[minutes]["ratio"], THRESHOLDS[minutes]["min_value_krw"]
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
        recent = bars[i-19:i+1]
        peak_bar = max(recent, key=lambda r: (float(r["high_price"]), stamp(r)))
        hits.append({"id": f"{MARKET}:{minutes}:{int(start)}", "start": start,
                     "minutes": minutes, "ratio": ratio, "value": value,
                     "volume": float(row["candle_acc_trade_volume"]),
                     "change": (float(row["trade_price"]) / float(row["opening_price"]) - 1) * 100,
                     "close": float(row["trade_price"]), "high": float(row["high_price"]),
                     "low": float(row["low_price"]),
                     "reference_peak": float(peak_bar["high_price"]),
                     "reference_peak_start": stamp(peak_bar),
                     "peak_window_bars": len(recent),
                     "sparse": start - stamp(prior[0]) > 25 * minutes * 60,
                     "prior_high": max(float(r["high_price"]) for r in prior),
                     "prior_low": min(float(r["low_price"]) for r in prior)})
    return hits


def message(hits, ticker, now):
    price = float(ticker["trade_price"])
    details = "\n".join(
        f"발생 {datetime.fromtimestamp(h['start'], KST):%H:%M} {h['minutes']}분 · 거래량 {h['volume']:,.2f} ZIL · 직전 20개 완료봉 중앙값의 {h['ratio']:.1f}배 · {h['value']/10000:,.0f}만원"
        + ("(저유동성 기준)" if h["sparse"] else "")
        for h in hits[-3:])
    latest = max(hits, key=lambda h: h["start"])
    peak = latest["reference_peak"]
    peak_change = (price / peak - 1) * 100
    close_change = (price / latest["close"] - 1) * 100
    peak_time = datetime.fromtimestamp(latest["reference_peak_start"], KST)
    context = (f"급증봉 변동 {latest['change']:+.2f}% · 종가 {latest['close']:g}원\n"
               f"최근 {latest['peak_window_bars']}개 완료 5분봉 고점 {peak:g}원 "
               f"({peak_time:%m/%d %H:%M} 시작봉) 대비 {peak_change:+.2f}%\n"
               f"현재가는 급증봉 종가 대비 {close_change:+.2f}%입니다.")
    if price < peak:
        context += ("\n고점에서 되밀린 가격입니다. 거래량이 급증했더라도 "
                    "매도 거래를 포함하므로 상승 지속을 뜻하지 않습니다.")
    else:
        context += "\n현재가는 최근 완료봉의 관측 고점 이상입니다."
    return ("[질리카 거래량 급증]\n"
            f"확인 {datetime.fromtimestamp(now, KST):%m/%d %H:%M:%S} KST · 현재 {price:g}원\n"
            f"{details}\n{context}\n완료봉 거래량 관찰이며 매수 신호가 아닙니다.")


class ZilWatcher:
    def __init__(self, dispatcher, audit, client):
        self.dispatcher, self.audit, self.client = dispatcher, audit, client

    async def tick(self, now=None):
        fixed_now = now is not None
        now = time.time() if now is None else now
        if now >= end_at():
            STATUS.update(state="expired", checked_at=now)
            return False
        five = await self.client.candles(MARKET, "minute5", 100)
        checked = now if fixed_now else time.time()
        if checked >= end_at():
            STATUS.update(state="expired", checked_at=checked)
            return False
        hits = detect(five, 5, checked)
        seen = self.audit.get("zil_watch_sent") or {}
        hits = [h for h in hits if h["id"] not in seen]
        STATUS.update(state="running", checked_at=checked, new_events=len(hits),
                      latest_5m_start=max((stamp(r) for r in five), default=None))
        LOGGER.info("ZIL_WATCH_TICK interval=300 events=%s latest_5m=%s", len(hits), STATUS["latest_5m_start"])
        if not hits:
            return True
        ticker = await self.client.ticker(MARKET)
        if abs(checked - float(ticker["timestamp"]) / 1000) > 120:
            raise ValueError("stale ZIL ticker; delivery deferred")
        if self.dispatcher.mode != "telegram":
            raise RuntimeError("ZIL Telegram delivery unavailable")
        await self.dispatcher._deliver_telegram(message(hits, ticker, checked),
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
    STATUS.update(policy_version=POLICY_VERSION, direction_alerts=False,
                  thresholds=THRESHOLDS, peak_context=True, peak_window_bars=20)
    client = UpbitPublicClient()
    watcher = ZilWatcher(dispatcher, audit, client)
    LOGGER.info("ZIL_WATCH_STARTED interval=300 market=%s policy=%s thresholds=%s direction_alerts=false peak_context=true end=%s mode=%s",
                MARKET, POLICY_VERSION, THRESHOLDS, end_at(), dispatcher.mode)
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
            # Five-minute boundary + 3s lets REST publish the completed candle.
            await asyncio.sleep(INTERVAL_SECONDS - time.time() % INTERVAL_SECONDS + 3)
    finally:
        await client.close()
