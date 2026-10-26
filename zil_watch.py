"""Bounded, public-data ZIL volume watch; observations only, never orders."""
from __future__ import annotations

import asyncio
import logging
import os
import time
from datetime import datetime, timezone, timedelta
from decimal import Decimal, InvalidOperation

from upbit_client import UpbitPublicClient

LOGGER = logging.getLogger(__name__)
MARKET = "KRW-ZIL"
KST = timezone(timedelta(hours=9))
# Known Upbit termination time. May be overridden only after checking a new notice.
END_DEFAULT = "2026-11-16T15:00:00+09:00"
STATUS = {"state": "not_started", "interval_seconds": 60, "market": MARKET}
POLICY_VERSION = "fixed-volume-1.5x-v2"
MULTIPLIER = Decimal("1.5")


def volume_reference(minutes):
    """Require a persisted reference; never fall back to noisy rolling ratios."""
    if minutes not in (1, 5):
        raise ValueError("unsupported ZIL interval")
    prefix = f"ZIL_WATCH_REFERENCE_{minutes}M_"
    try:
        volume = Decimal(os.environ[prefix + "VOLUME"])
        start = int(os.environ[prefix + "START"])
    except (KeyError, ValueError, InvalidOperation) as exc:
        raise ValueError("ZIL volume reference is missing or invalid") from exc
    if not volume.is_finite() or volume <= 0 or start <= 0 or start % (minutes * 60):
        raise ValueError("invalid ZIL volume reference")
    return {"volume": str(volume), "start": start,
            "threshold": str(volume * MULTIPLIER)}


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


def detect(rows, minutes, now, reference=None):
    """Only a new completed volume >= 1.5 times the fixed reference qualifies."""
    reference = volume_reference(minutes) if reference is None else reference
    threshold = Decimal(reference["threshold"])
    bars = completed(rows, minutes, now)
    hits = []
    for row in bars:
        start = stamp(row)
        if not now - 180 < start + minutes * 60 <= now:
            continue  # Bounded recovery, not a replay of old alarms.
        if start < reference["start"] + minutes * 60:
            continue  # The reference must already have closed before this event.
        volume = Decimal(str(row["candle_acc_trade_volume"]))
        value = Decimal(str(row["candle_acc_trade_price"]))
        if not volume.is_finite() or not value.is_finite() or value < 0 or volume < threshold:
            continue
        hits.append({"id": f"{MARKET}:{minutes}:{int(start)}", "start": start,
                     "minutes": minutes, "ratio": float(volume / Decimal(reference["volume"])),
                     "value": float(value), "volume": str(volume),
                     "reference_volume": reference["volume"],
                     "reference_start": reference["start"], "threshold": reference["threshold"]})
    return hits


def message(hits, ticker, now):
    price = float(ticker["trade_price"])
    details = "\n".join(
        f"발생 {datetime.fromtimestamp(h['start'], KST):%H:%M} {h['minutes']}분 · 거래량 {float(h['volume']):,.2f} ZIL · 고정 기준의 {h['ratio']:.2f}배\n"
        f"기준 {datetime.fromtimestamp(h['reference_start'], KST):%m/%d %H:%M} {float(h['reference_volume']):,.2f} ZIL · 거래대금 {h['value']/10000:,.0f}만원"
        for h in hits[-3:])
    return ("[질리카 거래량 확대 | 고정 기준 1.5배 이상]\n"
            f"확인 {datetime.fromtimestamp(now, KST):%m/%d %H:%M:%S} KST · 현재 {price:g}원\n"
            f"{details}\n완료봉 거래량 관찰이며 매수 신호가 아닙니다.")


class ZilWatcher:
    def __init__(self, dispatcher, audit, client):
        self.dispatcher, self.audit, self.client = dispatcher, audit, client
        self.references = {m: volume_reference(m) for m in (1, 5)}

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
        hits = detect(one, 1, checked, self.references[1]) + detect(five, 5, checked, self.references[5])
        seen = self.audit.get("zil_watch_sent") or {}
        hits = [h for h in hits if h["id"] not in seen]
        STATUS.update(state="running", checked_at=checked, new_events=len(hits),
                      latest_1m_start=max((stamp(r) for r in one), default=None))
        LOGGER.info("ZIL_WATCH_TICK interval=60 events=%s latest_1m=%s", len(hits), STATUS["latest_1m_start"])
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
    try:
        references = {m: volume_reference(m) for m in (1, 5)}
    except ValueError:
        STATUS.update(state="blocked_invalid_volume_reference", policy_version=POLICY_VERSION)
        LOGGER.error("ZIL_WATCH_BLOCKED valid fixed volume references required")
        return
    STATUS.update(policy_version=POLICY_VERSION, direction_alerts=False,
                  multiplier=str(MULTIPLIER), references=references)
    client = UpbitPublicClient()
    watcher = ZilWatcher(dispatcher, audit, client)
    LOGGER.info("ZIL_WATCH_STARTED interval=60 market=%s policy=%s references=%s direction_alerts=false end=%s mode=%s",
                MARKET, POLICY_VERSION, references, end_at(), dispatcher.mode)
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
