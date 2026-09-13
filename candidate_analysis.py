"""Completed-candle, read-only screening for real-time entry candidates."""

from __future__ import annotations

import os
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal, ROUND_CEILING, ROUND_FLOOR, ROUND_HALF_UP
from statistics import mean, median
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
    return default if raw is None else raw.strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class CandidateConfig:
    enabled: bool
    confirm_seconds: int
    min_score: int
    cooldown_seconds: int
    valid_seconds: int
    max_day_change_pct: float
    max_rsi_1m: float
    max_rsi_5m: float
    min_orderbook_ratio: float
    hard_min_orderbook_ratio: float
    max_price_extension_pct: float
    min_completed_volume_ratio: float
    min_volume_vs_previous: float
    min_close_position: float
    max_upper_wick_ratio: float
    max_spread_pct: float
    hard_max_spread_pct: float
    min_trade_value_24h_krw: float
    min_resistance_room_pct: float
    hard_min_resistance_room_pct: float
    min_risk_reward: float
    max_stop_loss_pct: float
    max_btc_decline_pct: float
    orderbook_sample_count: int
    orderbook_sample_interval_seconds: float
    reentry_window_seconds: int
    reentry_cooldown_seconds: int
    watchlist_window_seconds: int
    raw_signal_target_pct: float
    raw_signal_stop_pct: float
    btc_weak_score_penalty: int
    day_overheat_score_penalty: int
    orderbook_score_penalty: int
    spread_score_penalty: int
    resistance_score_penalty: int

    @classmethod
    def from_env(cls) -> "CandidateConfig":
        return cls(
            enabled=_enabled("ENABLE_CANDIDATE_ANALYSIS"),
            confirm_seconds=max(5, min(120, _env_int("CANDIDATE_CONFIRM_SECONDS", 30))),
            min_score=max(50, min(100, _env_int("CANDIDATE_MIN_SCORE", 90))),
            cooldown_seconds=max(300, _env_int("CANDIDATE_COOLDOWN_SECONDS", 900)),
            valid_seconds=max(60, min(900, _env_int("CANDIDATE_VALID_SECONDS", 300))),
            max_day_change_pct=_env_float("CANDIDATE_MAX_DAY_CHANGE_PCT", 20.0),
            max_rsi_1m=_env_float("CANDIDATE_MAX_RSI_1M", 78.0),
            max_rsi_5m=_env_float("CANDIDATE_MAX_RSI_5M", 75.0),
            min_orderbook_ratio=_env_float("CANDIDATE_MIN_ORDERBOOK_RATIO", 0.8),
            hard_min_orderbook_ratio=_env_float(
                "CANDIDATE_HARD_MIN_ORDERBOOK_RATIO", 0.4
            ),
            max_price_extension_pct=_env_float(
                "CANDIDATE_MAX_PRICE_EXTENSION_PCT", 2.0
            ),
            min_completed_volume_ratio=_env_float(
                "CANDIDATE_MIN_COMPLETED_VOLUME_RATIO", 1.2
            ),
            min_volume_vs_previous=_env_float("CANDIDATE_MIN_VOLUME_VS_PREVIOUS", 0.65),
            min_close_position=_env_float("CANDIDATE_MIN_CLOSE_POSITION", 0.6),
            max_upper_wick_ratio=_env_float("CANDIDATE_MAX_UPPER_WICK_RATIO", 0.4),
            max_spread_pct=_env_float("CANDIDATE_MAX_SPREAD_PCT", 0.5),
            hard_max_spread_pct=_env_float(
                "CANDIDATE_HARD_MAX_SPREAD_PCT", 1.2
            ),
            min_trade_value_24h_krw=_env_float(
                "CANDIDATE_MIN_TRADE_VALUE_24H_KRW", 1_000_000_000
            ),
            min_resistance_room_pct=_env_float(
                "CANDIDATE_MIN_RESISTANCE_ROOM_PCT", 5.0
            ),
            hard_min_resistance_room_pct=_env_float(
                "CANDIDATE_HARD_MIN_RESISTANCE_ROOM_PCT", 2.5
            ),
            min_risk_reward=_env_float("CANDIDATE_MIN_RISK_REWARD", 2.0),
            max_stop_loss_pct=_env_float("CANDIDATE_MAX_STOP_LOSS_PCT", 3.0),
            max_btc_decline_pct=_env_float("CANDIDATE_MAX_BTC_DECLINE_PCT", -1.5),
            orderbook_sample_count=max(
                1, min(5, _env_int("CANDIDATE_ORDERBOOK_SAMPLE_COUNT", 3))
            ),
            orderbook_sample_interval_seconds=max(
                0.5,
                min(
                    10.0, _env_float("CANDIDATE_ORDERBOOK_SAMPLE_INTERVAL_SECONDS", 2.0)
                ),
            ),
            reentry_window_seconds=max(
                300, _env_int("CANDIDATE_REENTRY_WINDOW_SECONDS", 1800)
            ),
            reentry_cooldown_seconds=max(
                60, _env_int("CANDIDATE_REENTRY_COOLDOWN_SECONDS", 300)
            ),
            watchlist_window_seconds=max(
                3600, _env_int("CANDIDATE_WATCHLIST_WINDOW_SECONDS", 43200)
            ),
            raw_signal_target_pct=max(
                1.0, _env_float("CANDIDATE_RAW_SIGNAL_TARGET_PCT", 5.0)
            ),
            raw_signal_stop_pct=max(
                0.5, _env_float("CANDIDATE_RAW_SIGNAL_STOP_PCT", 3.0)
            ),
            btc_weak_score_penalty=max(
                0, _env_int("CANDIDATE_BTC_WEAK_SCORE_PENALTY", 10)
            ),
            day_overheat_score_penalty=max(
                0, _env_int("CANDIDATE_DAY_OVERHEAT_SCORE_PENALTY", 8)
            ),
            orderbook_score_penalty=max(
                0, _env_int("CANDIDATE_ORDERBOOK_SCORE_PENALTY", 4)
            ),
            spread_score_penalty=max(
                0, _env_int("CANDIDATE_SPREAD_SCORE_PENALTY", 4)
            ),
            resistance_score_penalty=max(
                0, _env_int("CANDIDATE_RESISTANCE_SCORE_PENALTY", 6)
            ),
        )

    def public(self) -> dict[str, Any]:
        return asdict(self)


def _completed(candles: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Upbit returns newest first; index zero can still be in progress."""
    return candles[1:]


def _volume_metrics(candles: list[dict[str, Any]]) -> tuple[float, float]:
    if len(candles) < 21:
        return 0.0, 0.0
    latest = float(candles[0]["candle_acc_trade_volume"])
    previous = float(candles[1]["candle_acc_trade_volume"])
    baseline = mean(float(c["candle_acc_trade_volume"]) for c in candles[1:21])
    return (
        latest / baseline if baseline else 0.0,
        latest / previous if previous else 0.0,
    )


def _candle_shape(candle: dict[str, Any]) -> tuple[float, float]:
    opening, high, low, close = (
        float(candle["opening_price"]),
        float(candle["high_price"]),
        float(candle["low_price"]),
        float(candle["trade_price"]),
    )
    span = high - low
    if span <= 0:
        return 1.0, 0.0
    return (close - low) / span, (high - max(opening, close)) / span


def _parse_time(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    return (
        parsed.replace(tzinfo=timezone.utc) if parsed.tzinfo is None else parsed
    ).astimezone(timezone.utc)


def _completed_after_signal(candle: dict[str, Any], alert_time: Any) -> bool:
    signal = _parse_time(alert_time)
    opened = _parse_time(candle.get("candle_date_time_utc") or candle.get("time_utc"))
    return (
        True
        if signal is None or opened is None
        else opened + timedelta(minutes=1) > signal
    )


def _ma20_slope(candles: list[dict[str, Any]]) -> float:
    if len(candles) < 21:
        return 0.0
    current = mean(float(c["trade_price"]) for c in candles[:20])
    previous = mean(float(c["trade_price"]) for c in candles[1:21])
    return ((current / previous) - 1) * 100 if previous else 0.0


def _atr(candles: list[dict[str, Any]], period: int = 14) -> float:
    ordered = list(reversed(candles[: period + 1]))
    values = []
    for previous, current in zip(ordered, ordered[1:]):
        high, low = float(current["high_price"]), float(current["low_price"])
        previous_close = float(previous["trade_price"])
        values.append(
            max(high - low, abs(high - previous_close), abs(low - previous_close))
        )
    return mean(values[-period:]) if values else 0.0


def _swing_highs(candles: list[dict[str, Any]]) -> list[float]:
    highs = [float(c["high_price"]) for c in reversed(candles)]
    return [
        highs[i]
        for i in range(2, len(highs) - 2)
        if highs[i] >= max(highs[i - 2 : i]) and highs[i] >= max(highs[i + 1 : i + 3])
    ]


def _resistances(
    current: float,
    ticker: dict[str, Any],
    five: list[dict[str, Any]],
    fifteen: list[dict[str, Any]],
) -> list[float]:
    levels = [
        *_swing_highs(five[:80]),
        *_swing_highs(fifteen[:80]),
        float(ticker.get("high_price") or 0),
    ]
    return sorted({round(x, 12) for x in levels if x > current * 1.001})


def _tick_size(orderbook: dict[str, Any], price: float) -> float:
    points = sorted(
        {
            float(u.get(k, 0))
            for u in orderbook.get("orderbook_units", [])
            for k in ("bid_price", "ask_price")
            if float(u.get(k, 0)) > 0
        }
    )
    differences = [b - a for a, b in zip(points, points[1:]) if b > a]
    if differences:
        return min(differences)
    return (
        1.0
        if price >= 100
        else 0.01 if price >= 10 else 0.001 if price >= 1 else 0.0001
    )


def _round_tick(value: float, tick: float, direction: str = "nearest") -> float:
    modes = {"down": ROUND_FLOOR, "up": ROUND_CEILING, "nearest": ROUND_HALF_UP}
    units = (Decimal(str(value)) / Decimal(str(tick))).to_integral_value(
        rounding=modes[direction]
    )
    return float(units * Decimal(str(tick)))


def _book_metrics(samples: list[dict[str, Any]]) -> tuple[float, float, list[float]]:
    ratios, spreads = [], []
    for sample in samples:
        ask, bid = float(sample.get("total_ask_size") or 0), float(
            sample.get("total_bid_size") or 0
        )
        if ask:
            ratios.append(bid / ask)
        units = sample.get("orderbook_units") or []
        if units:
            best_ask, best_bid = float(units[0].get("ask_price") or 0), float(
                units[0].get("bid_price") or 0
            )
            midpoint = (best_ask + best_bid) / 2
            if midpoint and best_ask >= best_bid:
                spreads.append((best_ask - best_bid) / midpoint * 100)
    return (
        median(ratios) if ratios else 0.0,
        max(spreads) if spreads else float("inf"),
        ratios,
    )


def evaluate_candidate(
    alert: dict[str, Any],
    ticker: dict[str, Any],
    orderbook: dict[str, Any],
    candles_1m: list[dict[str, Any]],
    candles_5m: list[dict[str, Any]],
    config: CandidateConfig,
    *,
    candles_15m: list[dict[str, Any]] | None = None,
    btc_ticker: dict[str, Any] | None = None,
    btc_candles_5m: list[dict[str, Any]] | None = None,
    btc_candles_15m: list[dict[str, Any]] | None = None,
    orderbook_samples: list[dict[str, Any]] | None = None,
) -> tuple[dict[str, Any] | None, list[str]]:
    """Evaluate a setup using only completed candles and persistent snapshots."""
    if alert.get("signal") not in {
        "price_volume_surge",
        "breakout",
        "consolidation_rebreakout",
    }:
        return None, ["상승 후보 신호가 아님"]
    if len(candles_1m) < 62 or len(candles_5m) < 62:
        return None, ["분봉 데이터 부족"]

    candles_15m = candles_15m or candles_5m
    btc_candles_5m = btc_candles_5m or candles_5m
    btc_candles_15m = btc_candles_15m or btc_candles_5m
    btc_ticker = btc_ticker or {"signed_change_rate": 0.0}
    c1, c5, c15 = (
        _completed(candles_1m),
        _completed(candles_5m),
        _completed(candles_15m),
    )
    b5, b15 = _completed(btc_candles_5m), _completed(btc_candles_15m)
    if min(map(len, (c1, c5, c15, b5, b15))) < 60:
        return None, ["완료봉 데이터 부족"]

    current, signal_price = float(ticker["trade_price"]), float(alert["price"])
    day_change = float(ticker.get("signed_change_rate", 0)) * 100
    trade_value_24h = float(
        ticker.get("acc_trade_price_24h") or config.min_trade_value_24h_krw
    )
    one, five, fifteen = analyze_candles(c1), analyze_candles(c5), analyze_candles(c15)
    btc_five, btc_fifteen = analyze_candles(b5), analyze_candles(b15)
    rsi1, rsi5 = float(one.get("rsi14") or 0), float(five.get("rsi14") or 0)
    extension = (current / signal_price - 1) * 100
    volume_ratio, volume_previous = _volume_metrics(c1)
    close_position, upper_wick = _candle_shape(c1[0])
    samples = orderbook_samples or [orderbook]
    book_ratio, spread, ratios = _book_metrics(samples)
    book_persistent = sum(x >= config.min_orderbook_ratio for x in ratios) >= max(
        1, len(ratios) // 2 + 1
    )
    hard_book_persistent = sum(
        x >= config.hard_min_orderbook_ratio for x in ratios
    ) >= max(1, len(ratios) // 2 + 1)
    completed_close = float(c1[0]["trade_price"])
    breakout = float(
        alert.get("breakout_level")
        or alert.get("consolidation_high")
        or signal_price * 0.995
    )
    btc_change = float(btc_ticker.get("signed_change_rate", 0)) * 100
    btc_bearish = (
        float(btc_five["latest_price"]) < float(btc_five["ma20"])
        and float(btc_fifteen["latest_price"]) < float(btc_fifteen["ma20"])
        and _ma20_slope(b5) < 0
        and _ma20_slope(b15) < 0
    )

    rejected: list[str] = []
    if not _completed_after_signal(c1[0], alert.get("time_utc")):
        rejected.append("신호 이후 완료된 1분봉 없음")
    if completed_close < breakout:
        rejected.append("완료 1분봉이 돌파선 아래 마감")
    if current < breakout * 0.998:
        rejected.append("돌파선 재지지 실패")
    day_overheated = day_change > config.max_day_change_pct
    if rsi1 > config.max_rsi_1m or rsi5 > config.max_rsi_5m:
        rejected.append(f"RSI 과열(1분 {rsi1:.1f}/5분 {rsi5:.1f})")
    if not hard_book_persistent:
        rejected.append(f"호가 지지 극단적 부족({book_ratio:.2f}배)")
    if spread > config.hard_max_spread_pct:
        rejected.append(f"호가 스프레드 극단적 과다({spread:.2f}%)")
    if trade_value_24h < config.min_trade_value_24h_krw:
        rejected.append(f"24시간 거래대금 부족({trade_value_24h:,.0f}원)")
    if extension > config.max_price_extension_pct:
        rejected.append(f"신호가 대비 추격 구간(+{extension:.1f}%)")
    if current < float(one["ma20"]) or current < float(five["ma20"]):
        rejected.append("완료봉 기준 단기 20이평 아래")
    if volume_ratio < config.min_completed_volume_ratio:
        rejected.append(f"완료 1분봉 거래량 부족({volume_ratio:.2f}배)")
    if volume_previous < config.min_volume_vs_previous:
        rejected.append(f"직전 봉 대비 거래량 급감({volume_previous:.2f}배)")
    if close_position < config.min_close_position:
        rejected.append(f"완료봉 종가 위치 약함({close_position:.2f})")
    if upper_wick > config.max_upper_wick_ratio:
        rejected.append(f"긴 윗꼬리({upper_wick:.2f})")
    btc_weak = btc_change <= config.max_btc_decline_pct or btc_bearish

    resistance_levels = _resistances(current, ticker, c5, c15)
    if not resistance_levels:
        rejected.append("검증 가능한 상단 저항 부재")
        return None, rejected
    resistance = resistance_levels[0]
    room = (resistance / current - 1) * 100
    if room < config.hard_min_resistance_room_pct:
        rejected.append(f"가까운 저항까지 여유 부족({room:.1f}%)")
    if rejected:
        return None, rejected

    tick = _tick_size(orderbook, current)
    entry_high = _round_tick(min(current, signal_price * 1.006), tick)
    entry_low = _round_tick(
        min(entry_high, max(float(one["ma20"]), breakout * 0.998)), tick
    )
    entry_mid = (entry_low + entry_high) / 2
    support = min(
        min(float(c["low_price"]) for c in c1[:6]), breakout, float(one["ma20"])
    )
    atr_risk = max(_atr(c1) * 1.2, _atr(c5) * 0.35)
    stop_raw = min(support * 0.998, entry_mid - atr_risk)
    stop = _round_tick(
        max(stop_raw, entry_mid * (1 - config.max_stop_loss_pct / 100)), tick, "down"
    )
    risk = max(entry_mid - stop, tick * 2)
    risk_reward = (resistance - entry_mid) / risk
    if risk_reward < config.min_risk_reward:
        return None, [f"가까운 저항 기준 손익비 부족({risk_reward:.2f})"]

    setup_score = {
        "consolidation_rebreakout": 25,
        "breakout": 22,
        "price_volume_surge": 15,
    }[str(alert["signal"])]
    volume_score = (
        20
        if volume_ratio >= 2 and volume_previous >= 0.8
        else 17 if volume_ratio >= 1.5 and volume_previous >= 0.7 else 14
    )
    trend_score = 0
    for summary, points in ((one, 5), (five, 5), (fifteen, 4)):
        trend_score += points if current >= float(summary["ma20"]) else 0
        trend_score += (
            2
            if summary.get("ma60") is not None
            and float(summary["ma20"]) >= float(summary["ma60"])
            else 0
        )
    trend_score = min(trend_score, 20)
    btc_strong = (
        float(btc_five["latest_price"]) >= float(btc_five["ma20"])
        and float(btc_fifteen["latest_price"]) >= float(btc_fifteen["ma20"])
        and _ma20_slope(b5) >= 0
    )
    liquid_market = trade_value_24h >= config.min_trade_value_24h_krw * 5
    market_score = 15 if btc_strong else 10
    if btc_weak:
        market_score = max(0, market_score - config.btc_weak_score_penalty)
    risk_notes: list[str] = []
    score_penalty = config.day_overheat_score_penalty if day_overheated else 0
    if not book_persistent:
        score_penalty += config.orderbook_score_penalty
        risk_notes.append(
            f"호가 지지 약함 -{config.orderbook_score_penalty}점({book_ratio:.2f}배)"
        )
    if spread > config.max_spread_pct:
        score_penalty += config.spread_score_penalty
        risk_notes.append(
            f"호가 스프레드 주의 -{config.spread_score_penalty}점({spread:.2f}%)"
        )
    if room < config.min_resistance_room_pct:
        score_penalty += config.resistance_score_penalty
        risk_notes.append(
            f"저항 여유 제한 -{config.resistance_score_penalty}점({room:.1f}%)"
        )
    score = min(
        100,
        max(
            0,
            setup_score
            + volume_score
            + trend_score
            + market_score
            + (12 if room >= 7 and risk_reward >= 2.5 else 10)
            + (12 if book_ratio >= 1.2 and spread <= 0.2 and liquid_market else 10)
            - score_penalty,
        ),
    )
    if score < config.min_score:
        return None, [f"후보 점수 부족({score}/{config.min_score})"]

    strong_extension = (
        alert.get("signal") == "consolidation_rebreakout"
        and score >= 95
        and room >= 7
        and risk_reward >= 2.5
        and volume_ratio >= 2
        and volume_previous >= 0.8
        and trend_score >= 18
        and not btc_weak
        and not day_overheated
        and book_persistent
        and spread <= config.max_spread_pct
    )
    if strong_extension:
        target1 = _round_tick(min(resistance, entry_mid * 1.07), tick, "down")
        target2 = _round_tick(entry_mid * 1.10, tick, "up")
        target_mode = "강한 추세 확장형"
    else:
        target1 = _round_tick(min(resistance, entry_mid * 1.03), tick, "down")
        target2 = _round_tick(entry_mid * 1.05, tick, "up")
        target_mode = "균형 위험비형"
    reasons = [
        "완료 1분봉 돌파 확정",
        "거래량 유지",
        "호가 지지 지속",
        f"저항 여유 {room:.1f}%",
    ]
    if alert.get("signal") == "consolidation_rebreakout":
        reasons.insert(
            0, f"{int(alert.get('consolidation_minutes') or 0)}분 횡보 상단 재돌파"
        )
    if alert.get("is_reentry"):
        reasons.insert(0, "돌파선 재지지 후 재진입")

    if btc_weak:
        risk_notes.append(
            f"BTC 약세 감점 -{config.btc_weak_score_penalty}점({btc_change:.2f}%)"
        )
    if day_overheated:
        risk_notes.append(
            f"당일 과열 감점 -{config.day_overheat_score_penalty}점({day_change:.1f}%)"
        )
    suggested_position_pct = 5 if risk_notes else 15 if score >= 95 else 10

    return {
        "time_utc": alert.get("time_utc"),
        "market": alert["market"],
        "signal": "entry_candidate",
        "label": "조건부 진입 후보",
        "source_signal": alert["signal"],
        "is_reentry": bool(alert.get("is_reentry")),
        "watchlist_recheck": bool(alert.get("watchlist_recheck")),
        "score": score,
        "condition_score": score,
        "current_price": current,
        "entry_low": entry_low,
        "entry_high": entry_high,
        "chase_limit": _round_tick(entry_high + risk * 0.35, tick, "up"),
        "stop_price": stop,
        "target_1": target1,
        "target_2": target2,
        "target_1_pct": round((target1 / entry_mid - 1) * 100, 2),
        "target_2_pct": round((target2 / entry_mid - 1) * 100, 2),
        "target_mode": target_mode,
        "resistance_price": resistance,
        "resistance_room_pct": round(room, 2),
        "risk_reward": round(risk_reward, 2),
        "breakout_level": breakout,
        "valid_seconds": config.valid_seconds,
        "suggested_position_pct": suggested_position_pct,
        "day_change_pct": round(day_change, 2),
        "rsi_1m": round(rsi1, 1),
        "rsi_5m": round(rsi5, 1),
        "orderbook_bid_ask_ratio": round(book_ratio, 2),
        "spread_pct": round(spread, 3),
        "completed_1m_volume_ratio": round(volume_ratio, 2),
        "volume_vs_previous": round(volume_previous, 2),
        "close_position": round(close_position, 2),
        "upper_wick_ratio": round(upper_wick, 2),
        "btc_day_change_pct": round(btc_change, 2),
        "btc_weak": btc_weak,
        "day_overheated": day_overheated,
        "risk_notes": risk_notes,
        "reasons": reasons[:5],
        "trade_value_24h_krw": round(trade_value_24h),
    }, []
