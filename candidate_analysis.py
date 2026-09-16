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
    survival_confirm_seconds: int
    fast_leader_enabled: bool
    fast_leader_confirm_seconds: int
    fast_leader_max_percentile: float
    fast_leader_min_value_ratio_10m: float
    fast_leader_min_value_ratio_30m: float
    fast_leader_max_extension_pct: float
    min_score: int
    cooldown_seconds: int
    repeat_cooldown_seconds: int
    valid_seconds: int
    max_day_change_pct: float
    max_rsi_1m: float
    max_rsi_5m: float
    hard_max_rsi_1m: float
    hard_max_rsi_5m: float
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
    hard_min_market_breadth_pct: float
    btc_weak_min_orderbook_ratio: float
    hot_rsi_1m: float
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
    rsi_score_penalty: int
    availability_balance_enabled: bool
    availability_max_soft_warnings: int
    availability_soft_penalty: int
    availability_min_volume_ratio: float
    availability_min_volume_vs_previous: float
    availability_min_close_position: float
    availability_max_upper_wick_ratio: float
    availability_resistance_floor_pct: float
    availability_min_score: int
    relative_strength_required: bool
    relative_strength_top_percent: float
    relative_strength_min_5m_pct: float
    relative_strength_score_bonus: int
    early_trend_required: bool
    early_leader_lane_enabled: bool
    early_leader_max_percentile: float
    early_leader_score_bonus: int
    early_leader_resistance_floor_pct: float
    breakout_resistance_cluster_pct: float
    require_first_retest: bool
    retest_tolerance_pct: float
    leader_watch_enabled: bool
    leader_watch_max_rechecks: int
    leader_pullback_min_pct: float
    leader_pullback_max_pct: float
    leader_reclaim_pct: float
    leader_recheck_cooldown_seconds: int
    trend_target_2_pct: float
    trend_target_3_pct: float
    trend_target_4_pct: float
    trend_tracking_seconds: int
    outcome_tracking_seconds: int

    @classmethod
    def from_env(cls) -> "CandidateConfig":
        return cls(
            enabled=_enabled("ENABLE_CANDIDATE_ANALYSIS"),
            confirm_seconds=max(5, min(120, _env_int("CANDIDATE_CONFIRM_SECONDS", 30))),
            survival_confirm_seconds=max(
                30, min(180, _env_int("CANDIDATE_SURVIVAL_CONFIRM_SECONDS", 60))
            ),
            fast_leader_enabled=_enabled("CANDIDATE_FAST_LEADER_ENABLED", True),
            fast_leader_confirm_seconds=max(
                5, min(20, _env_int("CANDIDATE_FAST_LEADER_CONFIRM_SECONDS", 6))
            ),
            fast_leader_max_percentile=max(
                1.0,
                min(
                    3.0,
                    _env_float("CANDIDATE_FAST_LEADER_MAX_PERCENTILE", 2.0),
                ),
            ),
            fast_leader_min_value_ratio_10m=max(
                2.0,
                _env_float("CANDIDATE_FAST_LEADER_MIN_VALUE_RATIO_10M", 5.0),
            ),
            fast_leader_min_value_ratio_30m=max(
                2.0,
                _env_float("CANDIDATE_FAST_LEADER_MIN_VALUE_RATIO_30M", 5.0),
            ),
            fast_leader_max_extension_pct=max(
                0.5,
                min(
                    3.0,
                    _env_float("CANDIDATE_FAST_LEADER_MAX_EXTENSION_PCT", 1.5),
                ),
            ),
            min_score=max(50, min(100, _env_int("CANDIDATE_MIN_SCORE", 90))),
            cooldown_seconds=max(300, _env_int("CANDIDATE_COOLDOWN_SECONDS", 900)),
            repeat_cooldown_seconds=max(
                3600, _env_int("CANDIDATE_REPEAT_COOLDOWN_SECONDS", 14400)
            ),
            valid_seconds=max(60, min(900, _env_int("CANDIDATE_VALID_SECONDS", 300))),
            max_day_change_pct=_env_float("CANDIDATE_MAX_DAY_CHANGE_PCT", 20.0),
            max_rsi_1m=_env_float("CANDIDATE_MAX_RSI_1M", 78.0),
            max_rsi_5m=_env_float("CANDIDATE_MAX_RSI_5M", 75.0),
            hard_max_rsi_1m=_env_float("CANDIDATE_HARD_MAX_RSI_1M", 88.0),
            hard_max_rsi_5m=_env_float("CANDIDATE_HARD_MAX_RSI_5M", 85.0),
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
            hard_min_market_breadth_pct=max(
                0.0,
                min(
                    100.0,
                    _env_float("CANDIDATE_HARD_MIN_MARKET_BREADTH_PCT", 35.0),
                ),
            ),
            btc_weak_min_orderbook_ratio=max(
                0.0,
                _env_float("CANDIDATE_BTC_WEAK_MIN_ORDERBOOK_RATIO", 1.0),
            ),
            hot_rsi_1m=max(
                50.0, min(100.0, _env_float("CANDIDATE_HOT_RSI_1M", 75.0))
            ),
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
            rsi_score_penalty=max(
                0, _env_int("CANDIDATE_RSI_SCORE_PENALTY", 4)
            ),
            availability_balance_enabled=_enabled(
                "CANDIDATE_AVAILABILITY_BALANCE_ENABLED", False
            ),
            availability_max_soft_warnings=max(
                0, min(3, _env_int("CANDIDATE_AVAILABILITY_MAX_SOFT_WARNINGS", 2))
            ),
            availability_soft_penalty=max(
                0, _env_int("CANDIDATE_AVAILABILITY_SOFT_PENALTY", 4)
            ),
            availability_min_volume_ratio=_env_float(
                "CANDIDATE_AVAILABILITY_MIN_VOLUME_RATIO", 0.85
            ),
            availability_min_volume_vs_previous=_env_float(
                "CANDIDATE_AVAILABILITY_MIN_VOLUME_VS_PREVIOUS", 0.45
            ),
            availability_min_close_position=_env_float(
                "CANDIDATE_AVAILABILITY_MIN_CLOSE_POSITION", 0.35
            ),
            availability_max_upper_wick_ratio=_env_float(
                "CANDIDATE_AVAILABILITY_MAX_UPPER_WICK_RATIO", 0.65
            ),
            availability_resistance_floor_pct=_env_float(
                "CANDIDATE_AVAILABILITY_RESISTANCE_FLOOR_PCT", 1.5
            ),
            availability_min_score=max(
                50,
                min(100, _env_int("CANDIDATE_AVAILABILITY_MIN_SCORE", 90)),
            ),
            relative_strength_required=_enabled(
                "CANDIDATE_RELATIVE_STRENGTH_REQUIRED", True
            ),
            relative_strength_top_percent=max(
                5.0,
                min(
                    40.0,
                    _env_float("CANDIDATE_RELATIVE_STRENGTH_TOP_PERCENT", 10.0),
                ),
            ),
            relative_strength_min_5m_pct=_env_float(
                "CANDIDATE_RELATIVE_STRENGTH_MIN_5M_PCT", 1.0
            ),
            relative_strength_score_bonus=max(
                0, _env_int("CANDIDATE_RELATIVE_STRENGTH_SCORE_BONUS", 8)
            ),
            early_trend_required=_enabled(
                "CANDIDATE_EARLY_TREND_REQUIRED", True
            ),
            early_leader_lane_enabled=_enabled(
                "CANDIDATE_EARLY_LEADER_LANE_ENABLED", True
            ),
            early_leader_max_percentile=max(
                1.0,
                min(
                    10.0,
                    _env_float("CANDIDATE_EARLY_LEADER_MAX_PERCENTILE", 5.0),
                ),
            ),
            early_leader_score_bonus=max(
                0, _env_int("CANDIDATE_EARLY_LEADER_SCORE_BONUS", 8)
            ),
            early_leader_resistance_floor_pct=max(
                0.5,
                _env_float(
                    "CANDIDATE_EARLY_LEADER_RESISTANCE_FLOOR_PCT", 1.0
                ),
            ),
            breakout_resistance_cluster_pct=max(
                0.5,
                min(
                    3.0,
                    _env_float(
                        "CANDIDATE_BREAKOUT_RESISTANCE_CLUSTER_PCT", 1.5
                    ),
                ),
            ),
            require_first_retest=_enabled("CANDIDATE_REQUIRE_FIRST_RETEST", True),
            retest_tolerance_pct=max(
                0.1, _env_float("CANDIDATE_RETEST_TOLERANCE_PCT", 0.8)
            ),
            leader_watch_enabled=_enabled("CANDIDATE_LEADER_WATCH_ENABLED", True),
            leader_watch_max_rechecks=max(
                1, min(10, _env_int("CANDIDATE_LEADER_WATCH_MAX_RECHECKS", 3))
            ),
            leader_pullback_min_pct=max(
                0.3, _env_float("CANDIDATE_LEADER_PULLBACK_MIN_PCT", 1.0)
            ),
            leader_pullback_max_pct=max(
                1.0, _env_float("CANDIDATE_LEADER_PULLBACK_MAX_PCT", 5.0)
            ),
            leader_reclaim_pct=max(
                0.2, _env_float("CANDIDATE_LEADER_RECLAIM_PCT", 0.5)
            ),
            leader_recheck_cooldown_seconds=max(
                60,
                _env_int("CANDIDATE_LEADER_RECHECK_COOLDOWN_SECONDS", 300),
            ),
            trend_target_2_pct=max(
                5.0, _env_float("CANDIDATE_TREND_TARGET_2_PCT", 10.0)
            ),
            trend_target_3_pct=max(
                10.0, _env_float("CANDIDATE_TREND_TARGET_3_PCT", 15.0)
            ),
            trend_target_4_pct=max(
                15.0, _env_float("CANDIDATE_TREND_TARGET_4_PCT", 20.0)
            ),
            trend_tracking_seconds=max(
                3600, _env_int("CANDIDATE_TREND_TRACKING_SECONDS", 21600)
            ),
            outcome_tracking_seconds=max(
                3600, _env_int("CANDIDATE_OUTCOME_TRACKING_SECONDS", 7200)
            ),
        )

    def public(self) -> dict[str, Any]:
        return asdict(self)


def _completed(
    candles: list[dict[str, Any]], minutes: int, now: datetime | None = None
) -> list[dict[str, Any]]:
    """Return completed candles without discarding a stale latest candle."""
    if not candles:
        return []
    opened = _parse_time(
        candles[0].get("candle_date_time_utc") or candles[0].get("time_utc")
    )
    if opened is None:
        return candles[1:]
    current_time = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    return candles if opened + timedelta(minutes=minutes) <= current_time else candles[1:]


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


def _completed_after_signal(
    candle: dict[str, Any], alert_time: Any, minimum_exposure_seconds: int = 20
) -> bool:
    """Require meaningful post-signal trading time in the confirming candle.

    A signal arriving just before a minute close must not reuse that almost
    entirely pre-signal candle as confirmation. Requiring a short exposure
    window preserves early-signal availability without the delay of always
    waiting for a second full candle.
    """
    signal = _parse_time(alert_time)
    opened = _parse_time(candle.get("candle_date_time_utc") or candle.get("time_utc"))
    if signal is None or opened is None:
        return True
    completed_at = opened + timedelta(minutes=1)
    return completed_at >= signal + timedelta(seconds=minimum_exposure_seconds)


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
    five_swings = _swing_highs(five[:80])
    repeated_five = [
        level
        for index, level in enumerate(five_swings)
        if any(
            abs(other / level - 1) <= 0.004
            for other_index, other in enumerate(five_swings)
            if other_index != index
        )
    ]
    fifteen_swings = _swing_highs(fifteen[:80])
    structural = [*repeated_five, *fifteen_swings]
    day_high = float(ticker.get("high_price") or 0)
    day_high_confirmed = day_high > current * 1.025 or any(
        abs(level / day_high - 1) <= 0.004 for level in structural if day_high > 0
    )
    levels = [*structural, *([day_high] if day_high_confirmed else [])]
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


def validate_candidate_survival(
    candidate: dict[str, Any],
    ticker: dict[str, Any],
    orderbook_samples: list[dict[str, Any]],
    candles_1m: list[dict[str, Any]],
    config: CandidateConfig,
) -> tuple[dict[str, Any], list[str]]:
    """Recheck only the conditions that can invalidate an already-built setup.

    Relative rank and short-window momentum naturally cool after the initial
    impulse. Requiring the complete entry screen again turned the survival
    delay into a second discovery test and removed otherwise intact setups.
    """
    c1 = _completed(candles_1m, 1)
    if len(c1) < 21:
        return {}, ["생존 확인용 완료봉 데이터 부족"]

    current = float(ticker["trade_price"])
    breakout = float(candidate["breakout_level"])
    completed_close = float(c1[0]["trade_price"])
    volume_ratio, volume_previous = _volume_metrics(c1)
    book_ratio, spread, ratios = _book_metrics(orderbook_samples)
    hard_book_persistent = sum(
        ratio >= config.hard_min_orderbook_ratio for ratio in ratios
    ) >= max(1, len(ratios) // 2 + 1)

    rejected: list[str] = []
    if current <= float(candidate["stop_price"]):
        rejected.append("계획 손절선 이하")
    if current >= float(candidate["chase_limit"]):
        rejected.append("추격금지선 도달")
    if completed_close < breakout or current < breakout * 0.998:
        rejected.append("돌파선 유지 실패")
    if volume_ratio < config.availability_min_volume_ratio:
        rejected.append(f"완료 1분봉 거래량 붕괴({volume_ratio:.2f}배)")
    if volume_previous < config.availability_min_volume_vs_previous:
        rejected.append(f"직전 봉 대비 거래량 급감({volume_previous:.2f}배)")
    if not hard_book_persistent:
        rejected.append(f"호가 지지 소멸({book_ratio:.2f}배)")
    if spread > config.hard_max_spread_pct:
        rejected.append(f"호가 스프레드 극단적 과다({spread:.2f}%)")

    return {
        "survival_price": current,
        "survival_completed_close": completed_close,
        "survival_volume_ratio": round(volume_ratio, 2),
        "survival_volume_vs_previous": round(volume_previous, 2),
        "survival_orderbook_ratio": round(book_ratio, 2),
        "survival_spread_pct": round(spread, 3),
    }, rejected


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
    """Evaluate a setup with persistent snapshots and lane-specific candles."""
    if alert.get("signal") not in {
        "price_volume_surge",
        "breakout",
        "consolidation_rebreakout",
        "leader_volume_acceleration",
    }:
        return None, ["상승 후보 신호가 아님"]
    if len(candles_1m) < 62 or len(candles_5m) < 62:
        return None, ["분봉 데이터 부족"]

    candles_15m = candles_15m or candles_5m
    btc_candles_5m = btc_candles_5m or candles_5m
    btc_candles_15m = btc_candles_15m or btc_candles_5m
    btc_ticker = btc_ticker or {"signed_change_rate": 0.0}
    c1, c5, c15 = (
        _completed(candles_1m, 1),
        _completed(candles_5m, 5),
        _completed(candles_15m, 15),
    )
    b5, b15 = _completed(btc_candles_5m, 5), _completed(btc_candles_15m, 15)
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
    relative_ready = bool(alert.get("relative_strength_ready"))
    relative_eligible = bool(alert.get("relative_strength_eligible"))
    early_trend = bool(alert.get("early_trend"))
    relative_percentile = float(alert.get("relative_strength_percentile") or 100.0)
    best_relative_percentile = min(
        relative_percentile,
        float(
            alert.get("best_relative_strength_percentile")
            or relative_percentile
        ),
    )
    leadership_persisted = bool(
        best_relative_percentile <= config.early_leader_max_percentile
        and relative_percentile <= config.relative_strength_top_percent
    )
    momentum_5m = float(alert.get("momentum_5m_pct") or 0.0)
    momentum_15m_value = alert.get("momentum_15m_pct")
    momentum_15m = (
        float(momentum_15m_value) if momentum_15m_value is not None else None
    )
    preleader_ratio_10m = float(alert.get("preleader_volume_ratio_10m") or 0.0)
    preleader_ratio_30m = float(alert.get("preleader_volume_ratio_30m") or 0.0)
    # The confirming candle must itself test the breakout area. Older lows may
    # predate the signal and would incorrectly classify a late chase as a retest.
    recent_retest_low = float(c1[0]["low_price"])
    retest_confirmed = bool(
        alert.get("is_reentry")
        or alert.get("pullback_retest")
        or (
            recent_retest_low
            <= breakout * (1 + config.retest_tolerance_pct / 100)
            and completed_close >= breakout
        )
    )
    fast_leader_core = bool(
        config.fast_leader_enabled
        and alert.get("fast_leader")
        and alert.get("signal") == "leader_volume_acceleration"
        and relative_ready
        and relative_eligible
        and relative_percentile <= config.fast_leader_max_percentile
        and early_trend
        and momentum_5m >= config.relative_strength_min_5m_pct
        and (momentum_15m is None or momentum_15m > 0)
        and preleader_ratio_10m >= config.fast_leader_min_value_ratio_10m
        and preleader_ratio_30m >= config.fast_leader_min_value_ratio_30m
    )
    early_leader_core = bool(
        config.early_leader_lane_enabled
        and relative_ready
        and relative_eligible
        # A leader can move a few ranks while the confirming minute closes.
        # Preserve a top-tier observation from this same in-flight analysis,
        # but require the fresh snapshot to remain inside the broader
        # relative-strength eligibility band.
        and leadership_persisted
        and early_trend
        and momentum_5m >= config.relative_strength_min_5m_pct
        and (momentum_15m is None or momentum_15m > 0)
        and retest_confirmed
    )

    rejected: list[str] = []
    soft_warnings: list[str] = []
    if relative_ready and config.relative_strength_required:
        if (
            not relative_eligible
            or relative_percentile > config.relative_strength_top_percent
        ):
            rejected.append(
                "전체 시장 상대강도 상위권 아님"
                f"({relative_percentile:.1f}백분위)"
            )
        if momentum_5m < config.relative_strength_min_5m_pct:
            rejected.append(f"5분 상대 모멘텀 부족({momentum_5m:+.2f}%)")
        if momentum_15m is not None and momentum_15m <= 0:
            rejected.append(f"15분 추세 미확인({momentum_15m:+.2f}%)")
        if config.early_trend_required and not early_trend:
            rejected.append("상승 초기 가속 구간 아님")
        if config.require_first_retest and not retest_confirmed and not fast_leader_core:
            rejected.append("첫 눌림·돌파선 재지지 미확인")
    confirmation_started_at = alert.get("confirmation_started_at_utc") or alert.get(
        "time_utc"
    )
    if not fast_leader_core and not _completed_after_signal(
        c1[0], confirmation_started_at
    ):
        rejected.append("신호 이후 확인시간 20초를 채운 완료 1분봉 없음")
    if not fast_leader_core and completed_close < breakout:
        rejected.append("완료 1분봉이 돌파선 아래 마감")
    if current < breakout * 0.998:
        rejected.append("돌파선 재지지 실패")
    day_overheated = day_change > config.max_day_change_pct
    elevated_risk = day_change >= 10.0 or rsi1 >= 70.0 or rsi5 >= 68.0
    early_leader_lane = bool(
        (early_leader_core or fast_leader_core)
        and (fast_leader_core or completed_close >= breakout)
        and current >= breakout * 0.998
        and current >= float(five["ma20"])
        and extension
        <= (
            config.fast_leader_max_extension_pct
            if fast_leader_core
            else config.max_price_extension_pct
        )
        and (
            fast_leader_core
            or (
                volume_ratio >= config.min_completed_volume_ratio
                and volume_previous >= config.min_volume_vs_previous
                and close_position >= config.min_close_position
                and upper_wick <= config.max_upper_wick_ratio
            )
        )
        and hard_book_persistent
        and spread <= config.hard_max_spread_pct
        and not day_overheated
    )
    # Availability balancing is allowed to collect mild misses first. Elevated
    # setups are narrowed again below to candle-shape warnings only, so volume,
    # trend and resistance safety floors remain strict.
    strict_quality = not config.availability_balance_enabled
    if rsi1 > config.hard_max_rsi_1m or rsi5 > config.hard_max_rsi_5m:
        rejected.append(f"RSI 과열(1분 {rsi1:.1f}/5분 {rsi5:.1f})")
    if spread > config.hard_max_spread_pct:
        rejected.append(f"호가 스프레드 극단적 과다({spread:.2f}%)")
    if trade_value_24h < config.min_trade_value_24h_krw:
        rejected.append(f"24시간 거래대금 부족({trade_value_24h:,.0f}원)")
    max_extension = (
        config.fast_leader_max_extension_pct
        if fast_leader_core
        else config.max_price_extension_pct
    )
    if extension > max_extension:
        rejected.append(f"신호가 대비 추격 구간(+{extension:.1f}%)")
    if current < float(five["ma20"]):
        rejected.append("완료봉 기준 5분 20이평 아래")
    elif current < float(one["ma20"]):
        message = "완료봉 기준 1분 20이평 아래"
        (rejected if strict_quality else soft_warnings).append(message)

    if not fast_leader_core and volume_ratio < config.availability_min_volume_ratio:
        rejected.append(f"완료 1분봉 거래량 절대 부족({volume_ratio:.2f}배)")
    elif not fast_leader_core and volume_ratio < config.min_completed_volume_ratio:
        message = f"완료 1분봉 거래량 다소 부족({volume_ratio:.2f}배)"
        (rejected if strict_quality else soft_warnings).append(message)

    if (
        not fast_leader_core
        and volume_previous < config.availability_min_volume_vs_previous
    ):
        rejected.append(f"직전 봉 대비 거래량 급감({volume_previous:.2f}배)")
    elif not fast_leader_core and volume_previous < config.min_volume_vs_previous:
        message = f"직전 봉 대비 거래량 감소({volume_previous:.2f}배)"
        (rejected if strict_quality else soft_warnings).append(message)

    if not fast_leader_core and close_position < config.availability_min_close_position:
        rejected.append(f"완료봉 종가 위치 매우 약함({close_position:.2f})")
    elif not fast_leader_core and close_position < config.min_close_position:
        message = f"완료봉 종가 위치 다소 약함({close_position:.2f})"
        (rejected if strict_quality else soft_warnings).append(message)

    if not fast_leader_core and upper_wick > config.availability_max_upper_wick_ratio:
        rejected.append(f"긴 윗꼬리({upper_wick:.2f})")
    elif not fast_leader_core and upper_wick > config.max_upper_wick_ratio:
        message = f"윗꼬리 주의({upper_wick:.2f})"
        (rejected if strict_quality else soft_warnings).append(message)

    btc_weak = btc_change <= config.max_btc_decline_pct or btc_bearish
    market_regime = str(alert.get("market_regime") or "neutral")
    market_breadth_5m_pct = float(alert.get("market_breadth_5m_pct") or 0.0)

    # Accuracy-first gates. These combinations produced high condition scores
    # despite poor follow-through in production. They are direct contradictions
    # to an actionable long entry and cannot be offset by setup/volume points.
    breadth_softened = False
    if (
        market_regime == "risk_off"
        and market_breadth_5m_pct < config.hard_min_market_breadth_pct
    ):
        if early_leader_lane:
            breadth_softened = True
        else:
            rejected.append(
                "시장 확산도 하드차단"
                f"(5분 상승 종목 {market_breadth_5m_pct:.1f}% < "
                f"{config.hard_min_market_breadth_pct:.1f}%)"
            )
    book_softened = False
    if not book_persistent:
        label = "매우 약함" if not hard_book_persistent else "약함"
        if early_leader_lane and hard_book_persistent:
            book_softened = True
        else:
            rejected.append(f"호가 지지 하드차단({label}, {book_ratio:.2f}배)")
    if btc_weak and book_ratio < config.btc_weak_min_orderbook_ratio:
        rejected.append(
            "BTC 약세·호가 약세 동시 발생"
            f"({btc_change:+.2f}%, {book_ratio:.2f}배)"
        )
    hot_book_floor = config.min_orderbook_ratio if fast_leader_core else 1.0
    if rsi1 >= config.hot_rsi_1m and book_ratio < hot_book_floor:
        rejected.append(
            f"단기 과열·호가 약세 동시 발생(RSI {rsi1:.1f}, {book_ratio:.2f}배)"
        )
    spread_softened = bool(
        early_leader_lane
        and config.max_spread_pct < spread <= config.hard_max_spread_pct
    )
    if spread > config.max_spread_pct and not spread_softened:
        rejected.append(f"호가 스프레드 허용치 초과({spread:.2f}%)")

    resistance_levels = _resistances(current, ticker, c5, c15)
    breakout_cluster_ignored = False
    if (
        early_leader_lane
        and (
            fast_leader_core
            or alert.get("signal") == "consolidation_rebreakout"
            or alert.get("leader_pullback_recheck")
        )
        and (fast_leader_core or retest_confirmed)
        and not btc_weak
    ):
        # Repeated swing highs immediately around the consolidation ceiling
        # describe the level being broken, not a separate overhead supply
        # zone. Counting them again as resistance rejected genuine rebreakouts
        # such as KRW-LSK. Only collapse the tight cluster; farther structural
        # resistance remains a hard risk input.
        cluster_ceiling = breakout * (
            1 + config.breakout_resistance_cluster_pct / 100
        )
        filtered_levels = [
            level for level in resistance_levels if level > cluster_ceiling
        ]
        breakout_cluster_ignored = len(filtered_levels) < len(resistance_levels)
        resistance_levels = filtered_levels
    resistance_confirmed = bool(resistance_levels)
    resistance = (
        resistance_levels[0]
        if resistance_confirmed
        else current * (1 + config.min_resistance_room_pct / 100)
    )
    room = (resistance / current - 1) * 100
    resistance_rescued = False
    leader_resistance_override = False
    if resistance_confirmed and room < config.hard_min_resistance_room_pct:
        leader_resistance_override = bool(
            early_leader_lane
            and room >= config.early_leader_resistance_floor_pct
            and (
                alert.get("signal") == "consolidation_rebreakout"
                or alert.get("leader_pullback_recheck")
                or alert.get("is_reentry")
            )
            and not btc_weak
            and not elevated_risk
        )
        resistance_rescued = bool(
            config.availability_balance_enabled
            and room >= config.availability_resistance_floor_pct
            and alert.get("signal") == "consolidation_rebreakout"
            and not elevated_risk
            and not btc_weak
            and volume_ratio >= 1.5
            and volume_previous >= 0.7
            and close_position >= 0.65
            and upper_wick <= 0.35
        )
        if not resistance_rescued and not leader_resistance_override:
            rejected.append(f"가까운 저항까지 여유 부족({room:.1f}%)")
        elif resistance_rescued:
            soft_warnings.append(f"가까운 저항 제한적 여유({room:.1f}%)")
    if len(soft_warnings) > config.availability_max_soft_warnings:
        rejected.append(
            "완화 가능 품질조건 동시 미달"
            f"({len(soft_warnings)}개/{config.availability_max_soft_warnings}개 허용)"
        )
    elevated_candle_balance = False
    if elevated_risk and soft_warnings:
        mild_candle_warnings = (
            "완료봉 종가 위치 다소 약함",
            "윗꼬리 주의",
        )
        elevated_candle_balance = bool(
            len(soft_warnings) <= config.availability_max_soft_warnings
            and all(
                warning.startswith(mild_candle_warnings)
                for warning in soft_warnings
            )
            and room >= config.min_resistance_room_pct
        )
        if not elevated_candle_balance:
            # Moderate heat must never soften volume, moving-average or nearby
            # resistance deficiencies. Preserve the concrete reasons in logs.
            rejected.extend(soft_warnings)
    if rejected:
        return None, rejected

    tick = _tick_size(orderbook, current)
    if fast_leader_core:
        entry_high = _round_tick(
            signal_price * (1 + config.fast_leader_max_extension_pct / 100),
            tick,
            "down",
        )
    else:
        entry_high = _round_tick(min(current, signal_price * 1.006), tick)
    entry_low = _round_tick(
        min(entry_high, max(float(one["ma20"]), breakout * 0.998)), tick
    )
    entry_tolerance = tick / 2
    if current < entry_low - entry_tolerance or current > entry_high + entry_tolerance:
        return None, [
            "분석 시점 현재가가 진입구간 밖"
            f"({current:g}원, {entry_low:g}~{entry_high:g}원)"
        ]
    entry_reference = current
    target_resistance = resistance
    if leader_resistance_override:
        target_resistance = max(resistance, entry_reference * 1.03)
    support = min(
        min(float(c["low_price"]) for c in c1[:6]), breakout, float(one["ma20"])
    )
    atr_risk = max(_atr(c1) * 1.2, _atr(c5) * 0.35)
    stop_raw = min(support * 0.998, entry_reference - atr_risk)
    stop = _round_tick(
        max(
            stop_raw,
            entry_reference * (1 - config.max_stop_loss_pct / 100),
        ),
        tick,
        "down",
    )
    risk = max(entry_reference - stop, tick * 2)
    risk_reward = (target_resistance - entry_reference) / risk
    if risk_reward < config.min_risk_reward:
        return None, [f"가까운 저항 기준 손익비 부족({risk_reward:.2f})"]

    setup_score = {
        "consolidation_rebreakout": 25,
        "breakout": 22,
        "price_volume_surge": 20,
        "leader_volume_acceleration": 20,
    }[str(alert["signal"])]
    if fast_leader_core:
        volume_score = 20
    else:
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
    market_score = 15 if btc_strong else 12
    if btc_weak:
        market_score = max(0, market_score - config.btc_weak_score_penalty)
    risk_notes: list[str] = list(soft_warnings)
    if breadth_softened:
        risk_notes.append(
            "시장 약세 중 상대강도 선도 유지: 시장 확산도는 감점 반영"
        )
    if leader_resistance_override:
        risk_notes.append(
            "근접 저항은 상위 선도주의 재돌파 대상으로 조건부 허용"
        )
    if breakout_cluster_ignored:
        risk_notes.append(
            "재돌파 기준선 인접 저항군은 동일 돌파 구간으로 병합"
        )
    if elevated_candle_balance:
        risk_notes.append("중간 과열 구간의 경미한 완료봉 품질 감점 허용")
    if fast_leader_core:
        risk_notes.append(
            "완료 1분봉 전 신속 검증형: 진입구간 이탈 시 추격하지 않고 첫 눌림 대기"
        )
    if not resistance_confirmed:
        risk_notes.append("반복 확인된 상단 구조 저항 없음")
    score_penalty = config.day_overheat_score_penalty if day_overheated else 0
    score_penalty += len(soft_warnings) * config.availability_soft_penalty
    regime_bonus = 3 if market_regime == "risk_on" else 0
    if market_regime == "risk_off":
        score_penalty += 6
        risk_notes.append(
            f"시장 확산도 약세 -6점(5분 상승 종목 {market_breadth_5m_pct:.1f}%)"
        )
    if not book_persistent:
        score_penalty += config.orderbook_score_penalty
        book_label = "매우 약함" if not hard_book_persistent else "약함"
        risk_notes.append(
            f"호가 지지 {book_label} -{config.orderbook_score_penalty}점({book_ratio:.2f}배)"
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
    rsi_soft = rsi1 > config.max_rsi_1m or rsi5 > config.max_rsi_5m
    if rsi_soft:
        score_penalty += config.rsi_score_penalty
        risk_notes.append(
            f"RSI 주의 -{config.rsi_score_penalty}점(1분 {rsi1:.1f}/5분 {rsi5:.1f})"
        )
    raw_change = float(alert.get("change_1m_pct") or 0.0)
    raw_volume_ratio = float(alert.get("volume_ratio_vs_previous_1m") or 0.0)
    impulse_bonus = (
        6
        if raw_change >= 2.0 and raw_volume_ratio >= 5.0
        else 3 if raw_change >= 1.5 and raw_volume_ratio >= 3.0 else 0
    )
    relative_strength_bonus = 0
    if relative_ready and relative_eligible:
        relative_strength_bonus = config.relative_strength_score_bonus
        if relative_percentile > 5.0:
            relative_strength_bonus = max(1, relative_strength_bonus - 2)
    early_leader_bonus = (
        config.early_leader_score_bonus if early_leader_lane else 0
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
            + impulse_bonus
            + relative_strength_bonus
            + early_leader_bonus
            + regime_bonus
            - score_penalty,
        ),
    )
    effective_min_score = config.min_score
    if config.availability_balance_enabled:
        effective_min_score = min(
            config.min_score, config.availability_min_score
        )
    if score < effective_min_score:
        return None, [f"후보 점수 부족({score}/{effective_min_score})"]

    availability_tier = score < config.min_score
    if availability_tier:
        # The lower score tier exists only to restore a small number of usable
        # messages. It must still pass every direct entry-safety boundary and
        # cannot borrow points from an overheated or weak market environment.
        availability_safe = bool(
            not elevated_risk
            and not btc_weak
            and not day_overheated
            and book_persistent
            and spread <= config.max_spread_pct
            and (
                not resistance_confirmed
                or room >= config.hard_min_resistance_room_pct
            )
            and len(soft_warnings) <= config.availability_max_soft_warnings
        )
        if not availability_safe:
            return None, [
                "가용성 보완 후보 안전조건 미달"
                f"({score}/{config.min_score})"
            ]
        risk_notes.append(
            f"가용성 보완형 - 정규 {config.min_score}점 미만, 직접 안전선 통과"
        )

    relative_trend_extension = bool(
        relative_ready
        and relative_eligible
        and relative_percentile <= 10.0
        and momentum_5m >= config.relative_strength_min_5m_pct
        and (momentum_15m is None or momentum_15m > 0)
        and (retest_confirmed or fast_leader_core)
        and score >= 90
        and trend_score >= 16
        and not btc_weak
        and not day_overheated
    )
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
    target3 = None
    target4 = None
    if relative_trend_extension:
        target1 = _round_tick(
            min(target_resistance, entry_reference * 1.03), tick, "down"
        )
        target2 = _round_tick(
            entry_reference * (1 + config.trend_target_2_pct / 100), tick, "up"
        )
        target_mode = "상대강도 추세추적형"
    elif strong_extension:
        target1 = _round_tick(
            min(target_resistance, entry_reference * 1.07), tick, "down"
        )
        target2 = _round_tick(entry_reference * 1.10, tick, "up")
        target_mode = "강한 추세 확장형"
    else:
        target1 = _round_tick(
            min(target_resistance, entry_reference * 1.03), tick, "down"
        )
        target2 = _round_tick(entry_reference * 1.05, tick, "up")
        target_mode = "균형 위험비형"
    if relative_trend_extension or strong_extension:
        target3 = _round_tick(
            entry_reference * (1 + config.trend_target_3_pct / 100), tick, "up"
        )
        target4 = _round_tick(
            entry_reference * (1 + config.trend_target_4_pct / 100), tick, "up"
        )
    reasons = [
        (
            "실시간 가격·호가 신속 생존 확인"
            if fast_leader_core
            else "완료 1분봉 돌파 확정"
        ),
        (
            "거래대금 선행 급가속 유지"
            if fast_leader_core
            else "거래량 유지"
        ),
        "호가 지지 지속" if book_persistent else "호가는 감점 보조지표",
        (
            f"저항 여유 {room:.1f}%"
            if resistance_confirmed
            else "신선한 당일 고가는 미확정 저항으로 분리"
        ),
    ]
    if impulse_bonus:
        reasons.insert(0, f"강한 가격·거래대금 유입 +{impulse_bonus}점")
    if alert.get("signal") == "consolidation_rebreakout":
        consolidation_minutes = int(alert.get("consolidation_minutes") or 0)
        reasons.insert(
            0,
            (
                f"{consolidation_minutes}분 횡보 상단 재돌파"
                if consolidation_minutes > 0
                else "횡보 상단 재돌파"
            ),
        )
    if alert.get("signal") == "leader_volume_acceleration":
        reasons.insert(0, "09시 전후 거래대금 선행 가속에서 조기 포착")
    if relative_ready and relative_eligible:
        reasons.insert(
            0,
            "상대강도 "
            f"{int(alert.get('relative_strength_rank') or 0)}/"
            f"{int(alert.get('relative_strength_universe') or 0)}위",
        )
    if retest_confirmed and relative_ready:
        reasons.insert(0, "첫 눌림·돌파선 재지지 확인")
    if early_trend and relative_ready:
        reasons.insert(0, "상승 초기 가속 구간")
    if early_leader_lane:
        reasons.insert(0, "상대강도 선도주 정밀 통과")
    if fast_leader_core:
        reasons.insert(0, "상대강도 상위 2% 초고속 검증 통과")
    if alert.get("is_reentry"):
        reasons.insert(0, "돌파선 재지지 후 재진입")
    if alert.get("leader_pullback_recheck"):
        reasons.insert(0, "상대강도 선도주 첫 눌림 후 재돌파")

    if btc_weak:
        risk_notes.append(
            f"BTC 약세 감점 -{config.btc_weak_score_penalty}점({btc_change:.2f}%)"
        )
    if day_overheated:
        risk_notes.append(
            f"당일 과열 감점 -{config.day_overheat_score_penalty}점({day_change:.1f}%)"
        )
    suggested_position_pct = (
        5
        if fast_leader_core or availability_tier or risk_notes
        else 15 if score >= 95 else 10
    )

    return {
        "time_utc": alert.get("time_utc"),
        "market": alert["market"],
        "signal": "entry_candidate",
        "label": "조건부 진입 후보",
        "source_signal": alert["signal"],
        "is_reentry": bool(alert.get("is_reentry")),
        "watchlist_recheck": bool(alert.get("watchlist_recheck")),
        "leader_pullback_recheck": bool(
            alert.get("leader_pullback_recheck")
        ),
        "selection_lane": (
            "fast_leader"
            if fast_leader_core
            else "early_leader" if early_leader_lane else "standard"
        ),
        "fast_leader": fast_leader_core,
        "score": score,
        "condition_score": score,
        "current_price": current,
        "entry_reference_price": entry_reference,
        "entry_low": entry_low,
        "entry_high": entry_high,
        "chase_limit": _round_tick(entry_high + risk * 0.35, tick, "up"),
        "stop_price": stop,
        "target_1": target1,
        "target_2": target2,
        "target_3": target3,
        "target_4": target4,
        "target_1_pct": round((target1 / entry_reference - 1) * 100, 2),
        "target_2_pct": round((target2 / entry_reference - 1) * 100, 2),
        "target_3_pct": (
            round((target3 / entry_reference - 1) * 100, 2)
            if target3 is not None
            else None
        ),
        "target_4_pct": (
            round((target4 / entry_reference - 1) * 100, 2)
            if target4 is not None
            else None
        ),
        "target_mode": target_mode,
        "trend_management": (
            "1차 목표 후 진입가 보호, 10%·15%·20% 추세 관찰"
            if relative_trend_extension or strong_extension
            else "고정 목표 관리"
        ),
        "resistance_price": resistance,
        "risk_reward_reference_price": target_resistance,
        "resistance_confirmed": resistance_confirmed,
        "resistance_room_pct": round(room, 2),
        "leader_resistance_override": leader_resistance_override,
        "breakout_cluster_ignored": breakout_cluster_ignored,
        "risk_reward": round(risk_reward, 2),
        "breakout_level": breakout,
        "valid_seconds": (
            min(config.valid_seconds, 120)
            if fast_leader_core
            else config.valid_seconds
        ),
        "suggested_position_pct": suggested_position_pct,
        "day_change_pct": round(day_change, 2),
        "relative_strength_ready": relative_ready,
        "relative_strength_eligible": relative_eligible,
        "early_trend": early_trend,
        "early_leader_lane": early_leader_lane,
        "relative_strength_rank": alert.get("relative_strength_rank"),
        "relative_strength_universe": alert.get("relative_strength_universe"),
        "relative_strength_percentile": (
            round(relative_percentile, 2) if relative_ready else None
        ),
        "best_relative_strength_percentile": (
            round(best_relative_percentile, 2) if relative_ready else None
        ),
        "leadership_persisted": leadership_persisted,
        "market_regime": market_regime,
        "market_breadth_5m_pct": round(market_breadth_5m_pct, 1),
        "market_median_5m_pct": alert.get("market_median_5m_pct"),
        "momentum_5m_pct": (
            round(momentum_5m, 2) if relative_ready else None
        ),
        "momentum_15m_pct": (
            round(momentum_15m, 2) if momentum_15m is not None else None
        ),
        "momentum_60m_pct": alert.get("momentum_60m_pct"),
        "first_retest_confirmed": retest_confirmed,
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
        "elevated_risk": elevated_risk,
        "elevated_candle_balance": elevated_candle_balance,
        "availability_balanced": bool(soft_warnings),
        "availability_tier": availability_tier,
        "risk_notes": risk_notes,
        "reasons": reasons[:5],
        "trade_value_24h_krw": round(trade_value_24h),
    }, []
