"""Deterministic descriptive indicators; no trade execution or price prediction."""

from __future__ import annotations

from math import sqrt
from statistics import mean, pstdev
from typing import Any


def _rsi(closes: list[float], period: int = 14) -> float | None:
    if len(closes) <= period:
        return None
    changes = [b - a for a, b in zip(closes, closes[1:])]
    sample = changes[-period:]
    gains = mean([max(x, 0.0) for x in sample])
    losses = mean([max(-x, 0.0) for x in sample])
    if losses == 0:
        return 100.0
    return 100.0 - (100.0 / (1.0 + gains / losses))


def analyze_candles(candles: list[dict[str, Any]]) -> dict[str, Any]:
    """Return reproducible indicators from Upbit candles (newest-first input)."""
    if len(candles) < 20:
        raise ValueError("At least 20 candles are required")

    ordered = list(reversed(candles))
    closes = [float(c["trade_price"]) for c in ordered]
    volumes = [float(c["candle_acc_trade_volume"]) for c in ordered]
    returns = [(b / a) - 1.0 for a, b in zip(closes, closes[1:]) if a > 0]

    last = closes[-1]
    ma20 = mean(closes[-20:])
    ma60 = mean(closes[-60:]) if len(closes) >= 60 else None
    rsi14 = _rsi(closes)
    volatility = pstdev(returns[-20:]) * sqrt(20) * 100 if len(returns) >= 20 else None
    average_volume = mean(volumes[-20:])
    volume_ratio = volumes[-1] / average_volume if average_volume else None

    if last > ma20 and (ma60 is None or ma20 > ma60):
        trend = "최근 이동평균 기준 상승 우위"
    elif last < ma20 and (ma60 is None or ma20 < ma60):
        trend = "최근 이동평균 기준 하락 우위"
    else:
        trend = "최근 이동평균 기준 혼조"

    return {
        "latest_price": last,
        "ma20": round(ma20, 8),
        "ma60": round(ma60, 8) if ma60 is not None else None,
        "rsi14": round(rsi14, 2) if rsi14 is not None else None,
        "twenty_period_volatility_pct": round(volatility, 2) if volatility is not None else None,
        "latest_volume_vs_20_average": round(volume_ratio, 2) if volume_ratio is not None else None,
        "trend_summary": trend,
        "disclaimer": "과거 공개 시세의 수학적 요약이며 매수·매도 권유나 미래 예측이 아닙니다.",
    }
