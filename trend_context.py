"""Higher-timeframe context from fresh, completed public candles only."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from statistics import mean
from typing import Any


def _opened(candle: dict[str, Any]) -> datetime | None:
    value = candle.get("candle_date_time_utc") or candle.get("time_utc")
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    return (
        parsed.replace(tzinfo=timezone.utc) if parsed.tzinfo is None else parsed
    ).astimezone(timezone.utc)


def completed_context_candles(
    candles: list[dict[str, Any]], minutes: int, now: datetime
) -> list[dict[str, Any]]:
    """Never infer a higher-timeframe trend from undated or incomplete bars."""
    dated = []
    for candle in candles:
        opened = _opened(candle)
        if opened is not None and opened + timedelta(minutes=minutes) <= now:
            dated.append((opened, candle))
    dated.sort(key=lambda item: item[0], reverse=True)
    if len(dated) < 21:
        return []
    # Sparse or stale data must not enable a longer holding exception.
    if now - (dated[0][0] + timedelta(minutes=minutes)) > timedelta(minutes=minutes):
        return []
    if any(
        newer[0] - older[0] != timedelta(minutes=minutes)
        for newer, older in zip(dated[:21], dated[1:21])
    ):
        return []
    return [candle for _, candle in dated]


def recent_minute_candles_contiguous(
    candles: list[dict[str, Any]], now: datetime, count: int = 10
) -> bool:
    """At least one execution in each of the last ten completed minutes.

    Do not impose a twenty-one minute no-gap rule for a ten-minute boundary.
    This does not prove uninterrupted tick-by-tick execution.
    """
    last_open = now.astimezone(timezone.utc).replace(second=0, microsecond=0) - timedelta(minutes=1)
    starts = {_opened(c) for c in candles}
    return all(last_open - timedelta(minutes=i) in starts for i in range(count))


def _confirmed_lows(candles: list[dict[str, Any]]) -> list[float]:
    """A low needs two completed bars on each side; no future confirmation."""
    lows = [float(c["low_price"]) for c in reversed(candles[:48])]
    return [
        lows[i]
        for i in range(2, len(lows) - 2)
        if lows[i] <= min(lows[i - 2 : i] + lows[i + 1 : i + 3])
        and lows[i] < max(lows[i - 2 : i])
        and lows[i] < max(lows[i + 1 : i + 3])
    ]


def _summary(candles: list[dict[str, Any]]) -> dict[str, Any]:
    closes = [float(c["trade_price"]) for c in candles]
    ma20, previous = mean(closes[:20]), mean(closes[1:21])
    lows = _confirmed_lows(candles)
    support = lows[-1] if lows else None
    above = closes[0] >= ma20
    rising = ma20 > previous
    intact = support is None or closes[0] >= support
    established = above and rising and closes[0] > closes[3] and intact
    status = (
        "상승 유지"
        if established
        else "회복 초기" if above else "하락 우위" if not rising else "눌림·혼조"
    )
    baseline = mean(float(c["candle_acc_trade_volume"]) for c in candles[1:21])
    return {
        "status": status,
        "close": closes[0],
        "ma20": round(ma20, 8),
        "ma20_slope_pct": round((ma20 / previous - 1) * 100, 6) if previous else 0,
        "established": established,
        "above_ma20": above,
        "support": support,
        "support_intact": intact,
        "last_volume_ratio": (
            round(float(candles[0]["candle_acc_trade_volume"]) / baseline, 3)
            if baseline
            else None
        ),
        "last_completed_candle_opened_at_utc": _opened(candles[0]).isoformat(),
    }


def higher_timeframe_context(
    candles_15m: list[dict[str, Any]],
    candles_60m: list[dict[str, Any]],
    candles_240m: list[dict[str, Any]],
    *,
    now: datetime | None = None,
) -> dict[str, Any]:
    now = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    fifteen = completed_context_candles(candles_15m, 15, now)
    hourly = completed_context_candles(candles_60m, 60, now)
    four_hour = completed_context_candles(candles_240m, 240, now)
    if not fifteen or not hourly:
        return {"ready": False, "status": "상위 시간봉 완료 데이터 부족"}
    f, h = _summary(fifteen), _summary(hourly)
    return {
        "ready": True,
        "status": h["status"],
        "hourly_established": h["established"],
        "fifteen_intact": f["support_intact"],
        "fifteen": f,
        "hourly": h,
        # A falling 4h MA must not veto a fresh 1h trend reversal.
        "four_hour": _summary(four_hour) if four_hour else {"status": "미확인"},
    }
