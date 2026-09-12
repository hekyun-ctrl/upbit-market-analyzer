"""Deterministic, read-only screening for real-time entry candidates."""

from __future__ import annotations

import os
from dataclasses import asdict, dataclass
from decimal import Decimal, ROUND_CEILING, ROUND_FLOOR, ROUND_HALF_UP
from statistics import mean
from typing import Any

from analysis import analyze_candles


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, str(default)))
    except ValueError:
        return default


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)))
    except ValueError:
        return default


def _enabled(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class CandidateConfig:
    """Non-secret controls for candidate screening and notification."""

    enabled: bool
    confirm_seconds: int
    min_score: int
    cooldown_seconds: int
    valid_seconds: int
    max_day_change_pct: float
    max_rsi_1m: float
    max_rsi_5m: float
    min_orderbook_ratio: float
    max_price_extension_pct: float

    @classmethod
    def from_env(cls) -> "CandidateConfig":
        return cls(
            enabled=_enabled("ENABLE_CANDIDATE_ANALYSIS"),
            confirm_seconds=max(
                5, min(120, _env_int("CANDIDATE_CONFIRM_SECONDS", 30))
            ),
            min_score=max(50, min(100, _env_int("CANDIDATE_MIN_SCORE", 80))),
            cooldown_seconds=max(
                300, _env_int("CANDIDATE_COOLDOWN_SECONDS", 900)
            ),
            valid_seconds=max(
                60, min(900, _env_int("CANDIDATE_VALID_SECONDS", 300))
            ),
            max_day_change_pct=_env_float("CANDIDATE_MAX_DAY_CHANGE_PCT", 20.0),
            max_rsi_1m=_env_float("CANDIDATE_MAX_RSI_1M", 78.0),
            max_rsi_5m=_env_float("CANDIDATE_MAX_RSI_5M", 75.0),
            min_orderbook_ratio=_env_float(
                "CANDIDATE_MIN_ORDERBOOK_RATIO", 0.65
            ),
            max_price_extension_pct=_env_float(
                "CANDIDATE_MAX_PRICE_EXTENSION_PCT", 2.5
            ),
        )

    def public(self) -> dict[str, Any]:
        return asdict(self)


def _completed_volume_ratio(candles: list[dict[str, Any]]) -> float:
    """Compare the last completed candle with its previous 20 candles."""
    if len(candles) < 22:
        return 0.0
    latest_completed = float(candles[1]["candle_acc_trade_volume"])
    baseline = mean(
        float(candle["candle_acc_trade_volume"]) for candle in candles[2:22]
    )
    return latest_completed / baseline if baseline > 0 else 0.0


def _tick_size(orderbook: dict[str, Any], price: float) -> float:
    """Infer the currently tradable KRW tick from adjacent public orderbook levels."""
    points: list[float] = []
    for unit in orderbook.get("orderbook_units", []):
        for key in ("bid_price", "ask_price"):
            value = float(unit.get(key, 0))
            if value > 0:
                points.append(value)
    unique = sorted(set(points))
    differences = [
        b - a for a, b in zip(unique, unique[1:]) if b - a > max(price * 1e-10, 0)
    ]
    if differences:
        return min(differences)
    if price >= 1000:
        return 1.0
    if price >= 100:
        return 0.1
    if price >= 10:
        return 0.01
    if price >= 1:
        return 0.001
    return 0.0001


def _round_tick(value: float, tick: float, direction: str = "nearest") -> float:
    decimal_value = Decimal(str(value))
    decimal_tick = Decimal(str(tick))
    mode = {
        "down": ROUND_FLOOR,
        "up": ROUND_CEILING,
        "nearest": ROUND_HALF_UP,
    }[direction]
    units = (decimal_value / decimal_tick).to_integral_value(rounding=mode)
    return float(units * decimal_tick)


def evaluate_candidate(
    alert: dict[str, Any],
    ticker: dict[str, Any],
    orderbook: dict[str, Any],
    candles_1m: list[dict[str, Any]],
    candles_5m: list[dict[str, Any]],
    config: CandidateConfig,
) -> tuple[dict[str, Any] | None, list[str]]:
    """Return a conditional entry plan only when all safety filters pass."""
    if alert.get("signal") not in {"price_volume_surge", "breakout"}:
        return None, ["상승 후보 신호가 아님"]
    if len(candles_1m) < 60 or len(candles_5m) < 60:
        return None, ["분봉 데이터 부족"]

    current = float(ticker["trade_price"])
    signal_price = float(alert["price"])
    day_change_pct = float(ticker.get("signed_change_rate", 0)) * 100
    one = analyze_candles(candles_1m)
    five = analyze_candles(candles_5m)
    rsi_1m = float(one.get("rsi14") or 0)
    rsi_5m = float(five.get("rsi14") or 0)
    total_ask = float(orderbook.get("total_ask_size") or 0)
    total_bid = float(orderbook.get("total_bid_size") or 0)
    book_ratio = total_bid / total_ask if total_ask > 0 else 0.0
    extension_pct = ((current / signal_price) - 1) * 100
    volume_ratio = _completed_volume_ratio(candles_1m)

    rejected: list[str] = []
    if day_change_pct > config.max_day_change_pct:
        rejected.append(f"일간 상승률 과다({day_change_pct:.1f}%)")
    if rsi_1m > config.max_rsi_1m:
        rejected.append(f"1분 RSI 과열({rsi_1m:.1f})")
    if rsi_5m > config.max_rsi_5m:
        rejected.append(f"5분 RSI 과열({rsi_5m:.1f})")
    if book_ratio < config.min_orderbook_ratio:
        rejected.append(f"매도호가 우세(매수/매도 {book_ratio:.2f}배)")
    if extension_pct > config.max_price_extension_pct:
        rejected.append(f"신호가 대비 추격 구간(+{extension_pct:.1f}%)")
    if extension_pct < -1.5:
        rejected.append(f"돌파 실패({extension_pct:.1f}%)")
    if current < float(one["ma20"]):
        rejected.append("1분 가격이 20이평 아래")
    if current < float(five["ma20"]):
        rejected.append("5분 가격이 20이평 아래")
    if rejected:
        return None, rejected

    score = 0
    reasons: list[str] = []
    if current >= float(one["ma20"]):
        score += 15
        reasons.append("1분 추세 상승")
    if float(one["ma20"]) >= float(one["ma60"]):
        score += 10
    if current >= float(five["ma20"]):
        score += 15
        reasons.append("5분 추세 상승")
    if float(five["ma20"]) >= float(five["ma60"]):
        score += 10

    if 45 <= rsi_1m <= 70:
        score += 10
    elif 30 <= rsi_1m <= 75:
        score += 5
    if 45 <= rsi_5m <= 68:
        score += 10
    elif 30 <= rsi_5m <= 72:
        score += 5

    if book_ratio >= 1.2:
        score += 15
        reasons.append("매수호가 우세")
    elif book_ratio >= 1.0:
        score += 12
        reasons.append("매수호가 지지")
    elif book_ratio >= 0.8:
        score += 8
    else:
        score += 3

    if volume_ratio >= 2.0:
        score += 10
        reasons.append("거래량 지속")
    elif volume_ratio >= 1.0:
        score += 7
    elif volume_ratio >= 0.6:
        score += 3

    if -0.5 <= extension_pct <= 1.2:
        score += 10
        reasons.append("추격 전 가격")
    else:
        score += 5
    if 0 <= day_change_pct <= 12:
        score += 5
    elif day_change_pct <= config.max_day_change_pct:
        score += 2

    score = min(score, 100)
    if score < config.min_score:
        return None, [f"후보 점수 부족({score}/{config.min_score})"]

    tick = _tick_size(orderbook, current)
    entry_high_raw = min(current, signal_price * 1.008)
    entry_low_raw = max(float(one["ma20"]), entry_high_raw * 0.996)
    entry_low = _round_tick(entry_low_raw, tick, "nearest")
    entry_high = _round_tick(entry_high_raw, tick, "nearest")
    if entry_low > entry_high:
        entry_low = entry_high
    entry_mid = (entry_low + entry_high) / 2

    recent_support = min(
        float(candle["low_price"]) for candle in candles_1m[:6]
    )
    technical_support = max(float(one["ma20"]), min(recent_support, entry_low))
    stop_raw = max(technical_support * 0.996, entry_mid * 0.985)
    stop_raw = min(stop_raw, entry_low * 0.992)
    stop = _round_tick(stop_raw, tick, "down")
    risk = max(entry_mid - stop, tick * 2)
    target_1 = _round_tick(entry_mid + risk * 1.3, tick, "up")
    target_2 = _round_tick(entry_mid + risk * 2.0, tick, "up")
    chase_limit = _round_tick(entry_high + risk * 0.5, tick, "up")

    return {
        "time_utc": alert.get("time_utc"),
        "market": alert["market"],
        "signal": "entry_candidate",
        "label": "조건부 진입 후보",
        "source_signal": alert["signal"],
        "score": score,
        "current_price": current,
        "entry_low": entry_low,
        "entry_high": entry_high,
        "chase_limit": chase_limit,
        "stop_price": stop,
        "target_1": target_1,
        "target_2": target_2,
        "valid_seconds": config.valid_seconds,
        "suggested_position_pct": 20 if score >= 90 else 15,
        "day_change_pct": round(day_change_pct, 2),
        "rsi_1m": round(rsi_1m, 1),
        "rsi_5m": round(rsi_5m, 1),
        "orderbook_bid_ask_ratio": round(book_ratio, 2),
        "completed_1m_volume_ratio": round(volume_ratio, 2),
        "reasons": reasons[:4],
    }, []
