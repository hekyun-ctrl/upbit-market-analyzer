"""Completed-candle, read-only screening for real-time entry candidates."""

from __future__ import annotations

import os
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal, ROUND_CEILING, ROUND_FLOOR, ROUND_HALF_UP
from math import isfinite
from statistics import mean, median, pstdev
from typing import Any

from analysis import analyze_candles
from trend_context import higher_timeframe_context, completed_context_candles, recent_minute_candles_contiguous
from exit_plan import exit_plan_metrics, execution_cost_metrics
from trade_flow import buying_persistent


def validate_btc_survival(
    ticker: dict[str, Any] | None,
    candles_5m: list[dict[str, Any]],
    config: CandidateConfig,
    *, as_of: datetime | None = None,
) -> tuple[dict[str, float], list[str]]:
    """Refresh market risk during the wait; live price is a protective guard."""
    bars = completed_context_candles(candles_5m, 5, as_of or datetime.now(timezone.utc))
    if not bars or not ticker or not ticker.get("trade_price"):
        return {}, ["생존 시점 BTC 최신 완료봉 미확인"]
    close = float(bars[0]["trade_price"])
    body = (close / float(bars[0]["opening_price"]) - 1) * 100
    window = (close / float(bars[3]["trade_price"]) - 1) * 100
    live = (float(ticker["trade_price"]) / close - 1) * 100
    rejected = []
    if body <= config.btc_crash_5m_pct or window <= config.btc_crash_15m_pct:
        rejected.append("생존 중 BTC 완료봉 급락")
    if live <= config.btc_crash_5m_pct:
        rejected.append("생존 중 BTC 실시간 급락 보호")
    return {
        "survival_btc_5m_pct": round(body, 3),
        "survival_btc_15m_pct": round(window, 3),
        "survival_btc_live_pct": round(live, 3),
    }, rejected


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
    persistent_leader_max_day_change_pct: float
    persistent_leader_min_15m_pct: float
    pullback_entry_only_enabled: bool
    pullback_survival_confirm_seconds: int
    pullback_watch_min_pct: float
    pullback_watch_max_pct: float
    pullback_watch_reclaim_pct: float
    reentry_max_drawdown_from_day_high_pct: float
    risk_off_exception_max_percentile: float
    risk_off_exception_min_5m_pct: float
    risk_off_fresh_leader_max_percentile: float
    risk_off_fresh_leader_min_5m_pct: float
    risk_off_low_room_score_cap: int
    min_score: int
    cooldown_seconds: int
    repeat_cooldown_seconds: int
    valid_seconds: int
    max_day_change_pct: float
    max_rsi_1m: float
    max_rsi_5m: float
    hard_max_rsi_1m: float
    hard_max_rsi_5m: float
    elite_retest_max_rsi_5m: float
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
    double_bb_enabled: bool
    double_bb_require_confirmation: bool
    double_bb_lookback: int
    double_bb_score_bonus: int
    double_bb_unconfirmed_penalty: int
    double_bb_reversal_wick_ratio: float
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
    higher_timeframe_enabled: bool = True
    higher_timeframe_refresh_seconds: int = 300
    higher_timeframe_tracking_seconds: int = 86400
    btc_crash_5m_pct: float = -0.8
    btc_crash_15m_pct: float = -1.5
    explosive_leader_enabled: bool = True
    explosive_leader_min_score: int = 80
    trade_flow_leader_enabled: bool = True
    trade_flow_leader_min_score: int = 86
    tick_spread_enabled: bool = True
    tick_spread_cost_buffer_pct: float = 0.2
    tick_spread_min_net_risk_reward: float = 1.0
    completed_structure_enabled: bool = False

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
                    10.0,
                    _env_float("CANDIDATE_FAST_LEADER_MAX_PERCENTILE", 10.0),
                ),
            ),
            fast_leader_min_value_ratio_10m=max(
                1.2,
                _env_float("CANDIDATE_FAST_LEADER_MIN_VALUE_RATIO_10M", 1.5),
            ),
            fast_leader_min_value_ratio_30m=max(
                1.0,
                _env_float("CANDIDATE_FAST_LEADER_MIN_VALUE_RATIO_30M", 1.2),
            ),
            fast_leader_max_extension_pct=max(
                0.5,
                min(
                    3.0,
                    _env_float("CANDIDATE_FAST_LEADER_MAX_EXTENSION_PCT", 1.5),
                ),
            ),
            persistent_leader_max_day_change_pct=max(
                20.0,
                min(
                    50.0,
                    _env_float("CANDIDATE_PERSISTENT_LEADER_MAX_DAY_CHANGE_PCT", 35.0),
                ),
            ),
            persistent_leader_min_15m_pct=max(
                1.0,
                _env_float("CANDIDATE_PERSISTENT_LEADER_MIN_15M_PCT", 3.0),
            ),
            pullback_entry_only_enabled=_enabled(
                "CANDIDATE_PULLBACK_ENTRY_ONLY_ENABLED", True
            ),
            pullback_survival_confirm_seconds=max(
                10,
                min(
                    60,
                    _env_int("CANDIDATE_PULLBACK_SURVIVAL_CONFIRM_SECONDS", 20),
                ),
            ),
            pullback_watch_min_pct=max(
                0.3, _env_float("CANDIDATE_PULLBACK_WATCH_MIN_PCT", 0.6)
            ),
            pullback_watch_max_pct=max(
                2.0, _env_float("CANDIDATE_PULLBACK_WATCH_MAX_PCT", 4.0)
            ),
            pullback_watch_reclaim_pct=max(
                0.2, _env_float("CANDIDATE_PULLBACK_WATCH_RECLAIM_PCT", 0.35)
            ),
            reentry_max_drawdown_from_day_high_pct=max(
                1.0,
                _env_float("CANDIDATE_REENTRY_MAX_DRAWDOWN_FROM_DAY_HIGH_PCT", 7.0),
            ),
            risk_off_exception_max_percentile=max(
                0.1,
                _env_float("CANDIDATE_RISK_OFF_EXCEPTION_MAX_PERCENTILE", 1.0),
            ),
            risk_off_exception_min_5m_pct=max(
                0.0,
                _env_float("CANDIDATE_RISK_OFF_EXCEPTION_MIN_5M_PCT", 2.0),
            ),
            risk_off_fresh_leader_max_percentile=max(
                1.0,
                min(
                    10.0,
                    _env_float("CANDIDATE_RISK_OFF_FRESH_LEADER_MAX_PERCENTILE", 5.0),
                ),
            ),
            risk_off_fresh_leader_min_5m_pct=max(
                0.5,
                _env_float("CANDIDATE_RISK_OFF_FRESH_LEADER_MIN_5M_PCT", 1.5),
            ),
            risk_off_low_room_score_cap=max(
                0,
                min(
                    100,
                    _env_int("CANDIDATE_RISK_OFF_LOW_ROOM_SCORE_CAP", 89),
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
            elite_retest_max_rsi_5m=max(
                85.0,
                min(95.0, _env_float("CANDIDATE_ELITE_RETEST_MAX_RSI_5M", 92.0)),
            ),
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
            hard_max_spread_pct=_env_float("CANDIDATE_HARD_MAX_SPREAD_PCT", 1.2),
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
            hot_rsi_1m=max(50.0, min(100.0, _env_float("CANDIDATE_HOT_RSI_1M", 75.0))),
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
            spread_score_penalty=max(0, _env_int("CANDIDATE_SPREAD_SCORE_PENALTY", 4)),
            resistance_score_penalty=max(
                0, _env_int("CANDIDATE_RESISTANCE_SCORE_PENALTY", 6)
            ),
            rsi_score_penalty=max(0, _env_int("CANDIDATE_RSI_SCORE_PENALTY", 4)),
            double_bb_enabled=_enabled("CANDIDATE_DOUBLE_BB_ENABLED", True),
            double_bb_require_confirmation=_enabled(
                "CANDIDATE_DOUBLE_BB_REQUIRE_CONFIRMATION", True
            ),
            double_bb_lookback=max(
                2, min(8, _env_int("CANDIDATE_DOUBLE_BB_LOOKBACK", 4))
            ),
            double_bb_score_bonus=max(
                0, _env_int("CANDIDATE_DOUBLE_BB_SCORE_BONUS", 6)
            ),
            double_bb_unconfirmed_penalty=max(
                0, _env_int("CANDIDATE_DOUBLE_BB_UNCONFIRMED_PENALTY", 6)
            ),
            double_bb_reversal_wick_ratio=max(
                0.1,
                min(
                    0.9,
                    _env_float("CANDIDATE_DOUBLE_BB_REVERSAL_WICK_RATIO", 0.35),
                ),
            ),
            availability_balance_enabled=_enabled(
                "CANDIDATE_AVAILABILITY_BALANCE_ENABLED", True
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
                min(100, _env_int("CANDIDATE_AVAILABILITY_MIN_SCORE", 86)),
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
            early_trend_required=_enabled("CANDIDATE_EARLY_TREND_REQUIRED", True),
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
                _env_float("CANDIDATE_EARLY_LEADER_RESISTANCE_FLOOR_PCT", 1.0),
            ),
            breakout_resistance_cluster_pct=max(
                0.5,
                min(
                    3.0,
                    _env_float("CANDIDATE_BREAKOUT_RESISTANCE_CLUSTER_PCT", 1.5),
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
            higher_timeframe_enabled=_enabled(
                "CANDIDATE_HIGHER_TIMEFRAME_ENABLED", True
            ),
            higher_timeframe_refresh_seconds=max(
                60, _env_int("CANDIDATE_HIGHER_TIMEFRAME_REFRESH_SECONDS", 300)
            ),
            higher_timeframe_tracking_seconds=max(
                21600, _env_int("CANDIDATE_HIGHER_TIMEFRAME_TRACKING_SECONDS", 86400)
            ),
            btc_crash_5m_pct=min(-0.1, _env_float("CANDIDATE_BTC_CRASH_5M_PCT", -0.8)),
            btc_crash_15m_pct=min(
                -0.2, _env_float("CANDIDATE_BTC_CRASH_15M_PCT", -1.5)
            ),
            trade_flow_leader_min_score=max(86, min(100, _env_int("CANDIDATE_TRADE_FLOW_LEADER_MIN_SCORE", 86))),
            trade_flow_leader_enabled=_enabled("CANDIDATE_TRADE_FLOW_LEADER_ENABLED", True),
            tick_spread_enabled=_enabled("CANDIDATE_TICK_SPREAD_ENABLED", True),
            tick_spread_cost_buffer_pct=max(0.2, _env_float("CANDIDATE_TICK_SPREAD_COST_BUFFER_PCT", 0.2)),
            tick_spread_min_net_risk_reward=max(1.0, _env_float("CANDIDATE_TICK_SPREAD_MIN_NET_RISK_REWARD", 1.0)),
            completed_structure_enabled=_enabled("CANDIDATE_COMPLETED_STRUCTURE_ENABLED", False),
            explosive_leader_enabled=_enabled("CANDIDATE_EXPLOSIVE_LEADER_ENABLED", True),
            explosive_leader_min_score=max(80, min(100, _env_int("CANDIDATE_EXPLOSIVE_LEADER_MIN_SCORE", 80))),
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
    # A REST bundle can cross a minute boundary after its as-of timestamp.
    # More than its first candle may then be unfinished at that timestamp.
    return [c for c in candles if (
        (start := _parse_time(c.get("candle_date_time_utc") or c.get("time_utc")))
        is not None and start + timedelta(minutes=minutes) <= current_time
    )]


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


def _trade_value_metrics(candles: list[dict[str, Any]]) -> tuple[float, float]:
    """Compare completed-candle KRW turnover with the preceding 20-bar baseline."""
    if len(candles) < 21:
        return 0.0, 0.0
    values = [
        float(c.get("candle_acc_trade_price") or c.get("trade_value") or 0.0)
        for c in candles[:21]
    ]
    latest, previous = values[0], values[1]
    baseline = mean(values[1:21])
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


def _bollinger_at(
    candles: list[dict[str, Any]],
    offset: int,
    period: int,
    deviation: float,
    source_key: str,
) -> tuple[float, float, float] | None:
    """Return basis/upper/lower for newest-first completed candles."""
    window = candles[offset : offset + period]
    if len(window) < period:
        return None
    values = [float(candle[source_key]) for candle in window]
    basis = mean(values)
    width = pstdev(values) * deviation
    return basis, basis + width, basis - width


def _double_bollinger_context(
    candles: list[dict[str, Any]],
    breakout: float,
    *,
    lookback: int = 4,
    retest_tolerance_pct: float = 0.8,
    reversal_wick_ratio: float = 0.35,
) -> dict[str, Any]:
    """Classify the 4/4-open + 20/2-close double-BB setup."""
    if len(candles) < 20 + lookback:
        return {
            "ready": False,
            "status": "WB 데이터 부족",
            "confirmed": False,
            "true_breakout": False,
            "first_retest": False,
            "fake_breakout": False,
        }

    snapshots: list[dict[str, Any]] = []
    for offset in range(lookback):
        fast = _bollinger_at(candles, offset, 4, 4.0, "opening_price")
        standard = _bollinger_at(candles, offset, 20, 2.0, "trade_price")
        if fast is None or standard is None:
            continue
        candle = candles[offset]
        opening = float(candle["opening_price"])
        high = float(candle["high_price"])
        low = float(candle["low_price"])
        close = float(candle["trade_price"])
        span = max(high - low, 0.0)
        upper_wick = (high - max(opening, close)) / span if span else 0.0
        fast_upper, standard_upper = fast[1], standard[1]
        preceding = candles[offset + 1 : offset + 21]
        prior_high = max(float(item["high_price"]) for item in preceding)
        above_both = close > fast_upper and close > standard_upper
        touched_both = high >= fast_upper and high >= standard_upper
        structure_broken = close > prior_high
        snapshots.append(
            {
                "offset": offset,
                "close": close,
                "low": low,
                "fast_upper": fast_upper,
                "standard_upper": standard_upper,
                "prior_high": prior_high,
                "upper_wick": upper_wick,
                "true_breakout": above_both and structure_broken,
                "fake_breakout": bool(
                    touched_both
                    and not above_both
                    and not structure_broken
                    and upper_wick >= reversal_wick_ratio
                ),
            }
        )

    latest = snapshots[0]
    recent_breakout = next(
        (item for item in snapshots if item["true_breakout"]), None
    )
    retest_level = breakout
    if recent_breakout is not None:
        retest_level = max(breakout, float(recent_breakout["prior_high"]))
    # A first retest is a single pullback episode after a genuine WB break.
    # Every intervening close must hold the anchor; a later second touch after
    # leaving the zone must not be relabelled as the first pullback.
    first_retest = False
    retest_diagnostic = "no_recent_dual_band_breakout"
    first_touch_offset = None
    if recent_breakout is not None and int(recent_breakout["offset"]) > 0:
        newer = list(reversed(snapshots[:int(recent_breakout["offset"])]))
        floor = retest_level * (1 - retest_tolerance_pct / 100)
        ceiling = retest_level * (1 + retest_tolerance_pct / 100)
        broken = any(float(item["close"]) < retest_level or float(item["low"]) < floor for item in newer)
        touches = [i for i, item in enumerate(newer) if float(item["low"]) <= ceiling]
        if broken:
            retest_diagnostic = "intervening_support_break"
        elif not touches:
            retest_diagnostic = "no_actual_zone_contact"
        else:
            first, last = touches[0], touches[-1]
            first_touch_offset = newer[first]["offset"]
            continuous_episode = touches == list(range(first, last + 1))
            recovered = (last == len(newer) - 1 or (
                last == len(newer) - 2
                and float(latest["low"]) >= float(newer[last]["low"])
                and float(latest["close"]) > float(newer[last]["close"])))
            # Max two contact bars plus the immediate recovery confirmation.
            first_retest = continuous_episode and last - first <= 1 and recovered
            retest_diagnostic = "first_pullback_confirmed" if first_retest else "repeated_or_expired_pullback"

    true_breakout = bool(latest["true_breakout"])
    fake_breakout = bool(latest["fake_breakout"] and not first_retest)
    confirmed = true_breakout or first_retest
    if first_retest:
        status = "WB 동시 돌파 후 첫 눌림 재지지"
    elif true_breakout:
        status = "WB 두 상단·직전 매물대 동시 돌파"
    elif fake_breakout:
        status = "WB 상단 접촉 후 밴드 복귀(가짜 돌파 의심)"
    else:
        status = "WB 동시 돌파 미확정"
    return {
        "ready": True,
        "status": status,
        "confirmed": confirmed,
        "true_breakout": true_breakout,
        "first_retest": first_retest,
        "fake_breakout": fake_breakout,
        "fast_upper": round(float(latest["fast_upper"]), 12),
        "standard_upper": round(float(latest["standard_upper"]), 12),
        "structure_high": round(float(latest["prior_high"]), 12),
        "retest_level": round(retest_level, 12),
        "breakout_offset": recent_breakout["offset"] if recent_breakout is not None else None,
        "first_touch_offset": first_touch_offset,
        "retest_diagnostic": "fresh_dual_band_breakout" if true_breakout else retest_diagnostic,
        "above_fast_upper": float(latest["close"]) > float(latest["fast_upper"]),
        "above_standard_upper": float(latest["close"]) > float(latest["standard_upper"]),
        "structure_broken": float(latest["close"]) > float(latest["prior_high"]),
    }


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
    # One uninterrupted equal-high plateau is one touch, never many separate
    # tests of supply. A completely flat tape has no independently formed peak.
    peaks = []
    start = 0
    while start < len(highs):
        end = start + 1
        while end < len(highs) and highs[end] == highs[start]:
            end += 1
        left, right = highs[max(0, start - 2):start], highs[end:end + 2]
        if (len(left) == len(right) == 2 and highs[start] >= max(left + right)
                and min(left) < highs[start] and min(right) < highs[start]):
            peaks.append(highs[start])
        start = end
    return peaks


def _resistances(
    current: float,
    ticker: dict[str, Any],
    five: list[dict[str, Any]],
    fifteen: list[dict[str, Any]],
    *, completed_impulse_high: float | None = None,
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
    # A clean, completed breakout's own high is not independent historical
    # supply. Repeated historical peaks at that same price remain structural.
    if completed_impulse_high is not None and abs(day_high - completed_impulse_high) <= current * 1e-9:
        day_high_confirmed = False
    levels = [*structural, *([day_high] if day_high_confirmed else [])]
    return sorted({round(x, 12) for x in levels if x > current * 1.001})


def _krw_tick_size(price: float) -> float:
    """Official KRW grid (2025-07-31 policy); never infer a gap as one tick."""
    for floor, tick in ((1_000_000, 1000), (500_000, 500), (100_000, 100),
                        (50_000, 50), (10_000, 10), (5_000, 5), (100, 1),
                        (10, .1), (1, .01), (.1, .001), (.01, .0001),
                        (.001, .00001), (.0001, .000001), (.00001, .0000001)):
        if price >= floor:
            return float(tick)
    return .00000001


def _tick_size(orderbook: dict[str, Any], price: float) -> float:
    points = sorted(
        {
            Decimal(str(u.get(k, 0)))
            for u in orderbook.get("orderbook_units", [])
            for k in ("bid_price", "ask_price")
            if float(u.get(k, 0)) > 0
        }
    )
    differences = [b - a for a, b in zip(points, points[1:]) if b > a]
    if differences:
        return float(min(differences))
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


def _explosive_bar_context(candles_1m, candles_5m, candles_15m, now, *, impulse_min=3.0):
    """A completed breakout or its first two-bar retest, with no future highs."""
    five = completed_context_candles(candles_5m, 5, now)
    fifteen = completed_context_candles(candles_15m, 15, now)
    if not five or not fifteen or not recent_minute_candles_contiguous(candles_1m, now):
        return {"confirmed": False}
    volume5, previous5 = _volume_metrics(five)
    volume15 = _volume_metrics(fifteen)[0]
    position, wick = _candle_shape(five[0])
    latest_close = float(five[0]["trade_price"])
    for offset in range(3):
        bar = five[offset]
        prior_high = max(float(c["high_price"]) for c in five[offset + 1:offset + 13])
        impulse_volume = _volume_metrics(five[offset:])[0]
        impulse_position, impulse_wick = _candle_shape(bar)
        if not (float(bar["trade_price"]) > prior_high and impulse_volume >= impulse_min
                and impulse_position >= 0.75 and impulse_wick <= 0.25):
            continue
        # Only the first two completed bars can count as the first pullback.
        retest = bool(offset and float(five[0]["low_price"]) <= prior_high * 1.008
                      and all(float(c["trade_price"]) >= prior_high for c in five[:offset]))
        confirmed = bool((offset == 0 or retest) and latest_close >= prior_high
                         and volume5 >= (impulse_min if offset == 0 else 1.5)
                         and volume15 >= 1.0 and position >= 0.65 and wick <= 0.35)
        if confirmed:
            return {"confirmed": True, "retest": retest, "level": prior_high,
                    "close": latest_close, "low": float(five[0]["low_price"]),
                    "volume_5m": volume5, "volume_previous_5m": previous5,
                    "volume_15m": volume15, "close_position": position, "upper_wick": wick}
    return {"confirmed": False}


def _completed_retest_volume_context(candles_1m, candles_5m, candles_15m, now, anchor):
    """Volume/shape evidence for an already-proven WB first pullback.

    Do not demand a second, different 12-bar breakout pattern on top of WB.
    """
    five = completed_context_candles(candles_5m, 5, now)
    fifteen = completed_context_candles(candles_15m, 15, now)
    if len(five) < 21 or len(fifteen) < 21 or not recent_minute_candles_contiguous(candles_1m, now):
        return {"confirmed": False}
    volume5, previous5 = _volume_metrics(five)
    volume15 = _volume_metrics(fifteen)[0]
    position, wick = _candle_shape(five[0])
    return {"confirmed": bool(volume5 >= 1.5 and volume15 >= 1.0
                              and position >= .65 and wick <= .35
                              and float(five[0]["trade_price"]) >= anchor),
            "retest": True, "level": anchor, "close": float(five[0]["trade_price"]),
            "low": float(five[0]["low_price"]), "volume_5m": volume5,
            "volume_previous_5m": previous5, "volume_15m": volume15,
            "close_position": position, "upper_wick": wick}


def _completed_leader_context(candles_1m, candles_5m, candles_15m, now):
    """Bounded WB continuation, not a fabricated first pullback or hourly trend.

    Require a real dual-band breakout in the last hour, uninterrupted anchor
    support, three rising completed lows/closes, fresh volume and an intact
    rising 15m average. Live buy flow and execution/risk guards remain separate.
    """
    five = completed_context_candles(candles_5m, 5, now)
    fifteen = completed_context_candles(candles_15m, 15, now)
    missing = {"confirmed": False, "status": "완료봉 지속 구조 미확인"}
    if len(five) < 32 or len(fifteen) < 21 or not recent_minute_candles_contiguous(candles_1m, now):
        return missing
    volume5, previous5 = _volume_metrics(five)
    volume15 = _volume_metrics(fifteen)[0]
    position, wick = _candle_shape(five[0])
    recent = list(reversed(five[:3]))
    lows = [float(b["low_price"]) for b in recent]
    closes = [float(b["trade_price"]) for b in recent]
    rising = (all(b >= a for a, b in zip(lows, lows[1:])) and lows[-1] > lows[0]
              and all(b >= a for a, b in zip(closes, closes[1:])) and closes[-1] > closes[0])
    ma15 = mean(float(b["trade_price"]) for b in fifteen[:20])
    previous_ma15 = mean(float(b["trade_price"]) for b in fifteen[1:21])
    if not (volume5 >= 1.5 and volume15 >= 1.0 and rising and position >= .65 and wick <= .35
            and float(fifteen[0]["trade_price"]) > ma15 > previous_ma15
            and float(fifteen[0]["low_price"]) >= float(fifteen[1]["low_price"])):
        return missing
    anchor, offset = None, None
    # Inspect each historical breakout against its own preceding prices only.
    for i in range(12):
        proof = _double_bollinger_context(five[i:], 0, lookback=1)
        if not proof.get("true_breakout"):
            continue
        level = float(proof["structure_high"])
        if all(float(b["trade_price"]) >= level and float(b["low_price"]) >= level * .992
               for b in five[:i]):
            anchor, offset = level, i
            break
    if anchor is None:
        return missing
    return {"confirmed": True, "status": "WB 돌파 후 완료봉 저점 상승·체결 검증 대상",
            "retest": False, "level": anchor, "breakout_offset": offset,
            "close": closes[-1], "low": min(lows[-2:]),
            "volume_5m": volume5, "volume_previous_5m": previous5,
            "volume_15m": volume15, "close_position": position, "upper_wick": wick}


def _completed_structure_context(candles_1m, candles_5m, candles_15m, now, config):
    """Completed price structure is independent of moving band clearance.

    WB is reported as corroborating evidence, never fabricated. A first
    pullback needs a historical completed 20-bar breakout, bounded contact
    episode and contracting but non-vanishing volume. No forming bar is used.
    """
    five = completed_context_candles(candles_5m, 5, now)
    fifteen = completed_context_candles(candles_15m, 15, now)
    continuity = recent_minute_candles_contiguous(candles_1m, now)
    checks = {"history_5m": len(five) >= 21, "history_15m": len(fifteen) >= 21,
              "minute_continuity": continuity}
    if not checks["history_5m"] or not checks["history_15m"]:
        return {"confirmed": False, "status": "완료봉 데이터·시간 연속성 미확인",
                "as_of": now.timestamp(), "checks": checks,
                "blockers": [k for k, v in checks.items() if not v]}

    def volume_context(offset=0):
        selected = five[offset:]
        v5, previous = _volume_metrics(selected)
        cutoff = _parse_time(selected[0].get("candle_date_time_utc")) + timedelta(minutes=5)
        aligned = completed_context_candles(candles_15m, 15, min(now, cutoff)) if offset else fifteen
        v15 = _volume_metrics(aligned)[0]
        rolling, continuous = None, False
        if len(selected) >= 63:
            dates = [_parse_time(b.get("candle_date_time_utc")) for b in selected[:63]]
            expected = cutoff - timedelta(minutes=5)
            continuous = bool(dates[0] == expected and all(a is not None and b is not None
                and a - b == timedelta(minutes=5) for a, b in zip(dates, dates[1:])))
            if continuous:
                volumes = [float(b["candle_acc_trade_volume"]) for b in selected[:63]]
                baseline = mean(sum(volumes[i:i + 3]) for i in range(3, 63, 3))
                rolling = sum(volumes[:3]) / baseline if baseline > 0 else 0.0
        return {"volume_5m": v5, "volume_previous_5m": previous, "volume_15m": v15,
            "volume_mode": "standard" if v15 >= 1 else "completed_rolling_breakout",
            "rolling_15m_volume_ratio": rolling, "rolling_history_contiguous": continuous,
            "rolling_baseline_blocks": 20 if continuous else 0,
            "volume_15m_confirmed": v15 >= 1 or (continuous and rolling is not None and rolling >= 1)}

    volume = volume_context()
    wb = _double_bollinger_context(five, 0, lookback=1,
        retest_tolerance_pct=config.retest_tolerance_pct,
        reversal_wick_ratio=config.double_bb_reversal_wick_ratio)
    bar = five[0]
    position, wick = _candle_shape(bar)
    close = float(bar["trade_price"])
    prior_high = max(float(b["high_price"]) for b in five[1:21])
    price_breakout = close > prior_high and close > float(bar["opening_price"])
    wb_breakout = bool(wb.get("true_breakout") and not wb.get("fake_breakout"))
    # WB mode retains its real 20-bar anchor. The price mode uses the same
    # historical prices; a projected Bollinger upper is not a supply order.
    checks.update(price_structure=bool(price_breakout or wb_breakout),
                  volume_5m=volume["volume_5m"] >= 1.5,
                  volume_15m=bool(volume["volume_15m_confirmed"]),
                  close_quality=position >= .65 and wick <= .35)
    proof = {**volume, "confirmed": all(checks.values()), "checks": checks,
        "status": "완료 5분 WB 거래량 동반 돌파" if wb_breakout else "완료 5분 과거 가격대·거래량 돌파",
        "structure_mode": "wb_breakout" if wb_breakout else "price_breakout",
        "wb_confirmed": wb_breakout, "as_of": now.timestamp(), "retest": False,
        "level": prior_high, "low": float(bar["low_price"]), "close": close,
        "close_position": position, "upper_wick": wick}
    if not proof["confirmed"] and continuity:
        for offset in range(1, 4):
            if len(five) < offset + 21:
                break
            impulse = five[offset]
            anchor = max(float(b["high_price"]) for b in five[offset+1:offset+21])
            impulse_shape, impulse_wick = _candle_shape(impulse)
            impulse_volume = volume_context(offset)
            if not (float(impulse["trade_price"]) > anchor
                    and impulse_shape >= .65 and impulse_wick <= .35
                    and impulse_volume["volume_5m"] >= 1.5
                    and impulse_volume["volume_15m_confirmed"]):
                continue
            newer = list(reversed(five[:offset]))
            floor, ceiling = anchor * .992, anchor * 1.008
            touches = [i for i, b in enumerate(newer) if float(b["low_price"]) <= ceiling]
            contact = bool(touches and touches == list(range(touches[0], touches[-1]+1))
                           and touches[-1]-touches[0] <= 1 and touches[0] == 0
                           and (touches[-1] == len(newer)-1 or (
                               touches[-1] == len(newer)-2
                               and float(newer[-1]["low_price"]) >= float(newer[-2]["low_price"])
                               and close > float(newer[-2]["trade_price"]))))
            held = all(float(b["low_price"]) >= floor and float(b["trade_price"]) >= anchor for b in newer)
            impulse_units = float(impulse["candle_acc_trade_volume"])
            contraction = all(0 < float(b["candle_acc_trade_volume"]) < impulse_units for b in newer)
            retest_checks = {**checks, "price_structure": contact and held,
                "volume_5m": volume["volume_5m"] >= .5,
                "volume_15m": bool(volume["volume_15m_confirmed"]),
                "volume_contraction": contraction}
            if all(retest_checks.values()):
                impulse_wb = _double_bollinger_context(five[offset:], 0, lookback=1)
                proof.update(confirmed=True, checks=retest_checks, structure_mode="first_retest",
                    status="과거 가격대 돌파 후 첫 눌림·거래량 감소·재지지",
                    retest=True, wb_confirmed=bool(impulse_wb.get("true_breakout")),
                    level=anchor, breakout_offset=offset,
                    impulse_volume_5m=impulse_volume["volume_5m"],
                    impulse_volume_15m_verified=bool(impulse_volume["volume_15m_confirmed"]),
                    volume_contraction=True, first_contact_confirmed=True)
                break
    proof["blockers"] = [k for k, v in proof["checks"].items() if not v]
    if not proof["confirmed"]:
        proof["status"] = "완료 5분 구조·거래량 검증 미충족"
    return proof


def _structure_buy_flow(context, current, now):
    """Observed executed buys may explain resting asks; no persistence claim."""
    context = context or {}
    windows = context.get("windows") or []
    if (context.get("source") != "upbit_websocket_trade"
            or not context.get("ready") or len(windows) != 3
            or not 0 <= now - float(context.get("as_of") or 0) <= 10
            or any(int(w.get("count") or 0) < 2 for w in windows)):
        return False
    buy = sum(float(w.get("buy_krw") or 0) for w in windows)
    sell = sum(float(w.get("sell_krw") or 0) for w in windows)
    return bool(all(float(w.get("buy_krw") or 0) >= 0 and float(w.get("sell_krw") or 0) >= 0 for w in windows)
        and buy + sell >= 10_000_000 and buy >= .6 * (buy + sell)
        and float(windows[-1].get("close") or 0) >= current * .998
        and float(windows[-1].get("close") or 0) >= float(windows[0].get("close") or 0))


def structure_quote_supported(quote, config, *, flow_supported=False):
    """Execution depth plus a legal spread; use only for proven 5m structure."""
    ask = float(quote.get("ask_value_krw") or 0)
    bid = float(quote.get("bid_value_krw") or 0)
    spread = float(quote.get("spread_pct", float("inf")))
    spread_ok = spread <= config.max_spread_pct or bool(
        config.tick_spread_enabled and quote.get("one_tick")
        and quote.get("coarse_tick") and spread <= config.hard_max_spread_pct)
    return bool(quote.get("nearby_depth_supported") and min(bid, ask) >= 5_000_000
                and ask > 0 and bid / ask >= (.2 if flow_supported else .5) and spread_ok)


def _structure_book_context(samples, current, config, now, trade_flow_context=None):
    """Local repeated depth; a one-tick spread is costed, not auto rejected."""
    # Center each snapshot on its own quotes, not an earlier ticker price.
    quotes = []
    for sample in samples:
        units = sample.get("orderbook_units") or []
        midpoint = ((float(units[0].get("bid_price") or 0)
                     + float(units[0].get("ask_price") or 0)) / 2) if units else current
        quote = _quote_execution_context(sample, midpoint, config)
        if not current * .98 <= midpoint <= current * 1.02:
            quote["nearby_depth_supported"] = False
        quotes.append(quote)
    def ratio(q):
        return q.get("bid_value_krw", 0) / q["ask_value_krw"] if q.get("ask_value_krw", 0) > 0 else 0
    flow_supported = _structure_buy_flow(trade_flow_context, current, now.timestamp())
    supported = [structure_quote_supported(q, config, flow_supported=flow_supported) for q in quotes]
    floor_supported = [structure_quote_supported(q, config, flow_supported=True) for q in quotes]
    return {"confirmed": bool(len(quotes) >= 3 and all(floor_supported)
            and sum(supported) >= len(quotes) // 2 + 1),
        "as_of": now.timestamp(), "quotes": quotes, "sample_count": len(quotes),
        "mode": "executed_buy_share" if flow_supported else "local_depth",
        "flow_supported": flow_supported,
        "coarse_tick": any(q.get("coarse_tick") for q in quotes),
        "nearby_ratios": [ratio(q) for q in quotes]}


def structure_volume_verified(candidate, now):
    """Fail closed at delivery on stale, missing or changed volume evidence."""
    proof = candidate.get("survival_structure_context") or {}
    if not (proof.get("confirmed")
            and proof.get("structure_mode") in {"wb_breakout", "price_breakout", "first_retest"}
            and (proof.get("structure_mode") == "first_retest") == bool(proof.get("retest"))
            and 0 <= now - float(proof.get("as_of") or 0) <= 10
            and float(proof.get("volume_5m") or 0) >= (.5 if proof.get("structure_mode") == "first_retest" else 1.5)
            and float(proof.get("close_position") or 0) >= .65
            and float(proof.get("upper_wick", 1)) <= .35
            and 0 < float(proof.get("level") or 0) <= float(candidate.get("current_price") or 0)
            <= float(proof.get("close") or 0) * 1.005):
        return False
    if proof.get("retest"):
        if not (proof.get("structure_mode") == "first_retest"
                and proof.get("first_contact_confirmed") and proof.get("volume_contraction")
                and float(proof.get("impulse_volume_5m") or 0) >= 1.5
                and proof.get("impulse_volume_15m_verified")):
            return False
    elif not float(proof.get("close") or 0) > float(proof.get("level") or 0):
        return False
    if proof.get("volume_mode") == "standard":
        return float(proof.get("volume_15m") or 0) >= 1
    return bool(proof.get("volume_mode") == "completed_rolling_breakout"
        and proof.get("rolling_history_contiguous") and proof.get("rolling_baseline_blocks") == 20
        and float(proof.get("rolling_15m_volume_ratio") or 0) >= 1)


def _flow_entry_context(candles_1m, candles_5m, candles_15m, now, config):
    """Alternative evidence exclusively for the fresh executed-buying lane.

    A rolling 15 minutes is three *completed* five-minute bars, compared with
    20 preceding non-overlapping three-bar blocks. Never project a forming bar.
    A contracting first pullback needs an independently confirmed WB event.
    """
    strict = _explosive_bar_context(candles_1m, candles_5m, candles_15m, now, impulse_min=1.5)
    if strict.get("confirmed"):
        return {**strict, "volume_mode": "standard", "as_of": now.timestamp()}
    five = completed_context_candles(candles_5m, 5, now)
    fifteen = completed_context_candles(candles_15m, 15, now)
    missing = {"confirmed": False}
    if len(five) < 63 or len(fifteen) < 21 or not recent_minute_candles_contiguous(candles_1m, now):
        return missing
    dates = [datetime.fromisoformat(b["candle_date_time_utc"]).replace(tzinfo=timezone.utc) for b in five[:63]]
    last_open = now.astimezone(timezone.utc).replace(minute=now.minute // 5 * 5, second=0, microsecond=0) - timedelta(minutes=5)
    if dates[0] != last_open or any(a - b != timedelta(minutes=5) for a, b in zip(dates, dates[1:])):
        return missing
    volumes = [float(b["candle_acc_trade_volume"]) for b in five[:63]]
    baseline = mean(sum(volumes[i:i + 3]) for i in range(3, 63, 3))
    rolling = sum(volumes[:3]) / baseline if baseline > 0 else 0.0
    volume5, previous5 = _volume_metrics(five)
    volume15 = _volume_metrics(fifteen)[0]
    position, wick = _candle_shape(five[0])
    close = float(five[0]["trade_price"])
    prior_high = max(float(b["high_price"]) for b in five[1:13])
    mode, level, impulse, contraction = None, prior_high, volume5, None
    if volume5 >= 1.5 and rolling >= 1.0 and close > prior_high and position >= .75 and wick <= .25:
        mode = "completed_rolling_breakout"
    else:
        wb = _double_bollinger_context(five, 0, lookback=config.double_bb_lookback,
            retest_tolerance_pct=config.retest_tolerance_pct,
            reversal_wick_ratio=config.double_bb_reversal_wick_ratio)
        offset = wb.get("breakout_offset")
        if not wb.get("first_retest") or wb.get("fake_breakout") or offset not in (1, 2):
            return missing
        level = float(wb.get("retest_level") or 0)
        impulse = _volume_metrics(five[offset:])[0]
        contraction = volumes[0] / volumes[offset] if volumes[offset] > 0 else float("inf")
        if (.5 <= volume5 < 1.5 and impulse >= 1.5 and contraction <= .75
                and (volume15 >= 1.0 or rolling >= 1.0)
                and position >= .5 and wick <= .35 and close >= level > 0):
            mode = "contracting_first_retest"
    if mode is None:
        return missing
    return {"confirmed": True, "volume_mode": mode, "as_of": now.timestamp(),
            "retest": mode == "contracting_first_retest", "level": level,
            "close": close, "low": float(five[0]["low_price"]),
            "volume_5m": volume5, "volume_previous_5m": previous5,
            "volume_15m": volume15, "rolling_15m_volume_ratio": rolling,
            "impulse_volume_ratio": impulse, "pullback_to_impulse_volume_ratio": contraction,
            "close_position": position, "upper_wick": wick}


def flow_volume_verified(candidate, now):
    """Final delivery must use fresh survival evidence for alternative volume."""
    evidence = candidate.get("survival_flow_entry_context") or {}
    mode = candidate.get("flow_volume_mode", "standard")
    if mode == "standard":
        return (float(candidate.get("completed_5m_volume_ratio") or 0) >= 1.5
                and float(candidate.get("completed_15m_volume_ratio") or 0) >= 1.0)
    if (mode not in ("completed_rolling_breakout", "contracting_first_retest")
            or not evidence.get("confirmed") or evidence.get("volume_mode") != mode
            or not 0 <= now - float(evidence.get("as_of") or 0) <= 10
            or not float(evidence.get("level") or 0) <= float(candidate.get("current_price") or 0)
               <= float(evidence.get("close") or 0) * 1.005):
        return False
    v5, v15 = float(evidence.get("volume_5m") or 0), float(evidence.get("volume_15m") or 0)
    rolling = float(evidence.get("rolling_15m_volume_ratio") or 0)
    if mode == "completed_rolling_breakout":
        return v5 >= 1.5 and rolling >= 1 and not evidence.get("retest")
    return bool(evidence.get("retest") and .5 <= v5 < 1.5
                and float(evidence.get("impulse_volume_ratio") or 0) >= 1.5
                and evidence.get("pullback_to_impulse_volume_ratio") is not None
                and 0 <= float(evidence["pullback_to_impulse_volume_ratio"]) <= .75
                and (v15 >= 1 or rolling >= 1))


def _quote_execution_context(sample, current, config):
    units = sample.get("orderbook_units") or []
    missing = {"one_tick": False, "depth_supported": False, "spread_pct": float("inf")}
    if not units or current <= 0:
        return missing
    bid = float(units[0].get("bid_price") or 0)
    ask = float(units[0].get("ask_price") or 0)
    if (not all(isfinite(x) for x in (bid, ask, current))
            or not 0 < bid < ask or not bid * .98 <= current <= ask * 1.02):
        return missing
    tick = Decimal(str(_krw_tick_size(bid)))
    bid_d, ask_d = Decimal(str(bid)), Decimal(str(ask))
    one_tick = bool(bid_d % tick == 0 and ask_d - bid_d == tick
                    and ask_d % Decimal(str(_krw_tick_size(ask))) == 0)
    ratio, spread, _ = _book_metrics([sample])
    coarse = bool(config.tick_spread_enabled and one_tick
                  and config.max_spread_pct < spread <= config.hard_max_spread_pct)
    # For a legal coarse tick the closest quote can sit outside +/-0.5%.
    # Include that executable quote, not distant resting book liquidity.
    bid_floor = min(current * .995, bid) if coarse else current * .995
    ask_ceiling = max(current * 1.005, ask) if coarse else current * 1.005
    tolerance = max(current * 1e-10, 1e-12)
    bid_value = sum(float(u.get("bid_price") or 0) * float(u.get("bid_size") or 0)
                    for u in units if bid_floor - tolerance <= float(u.get("bid_price") or 0) <= min(current * 1.005, bid) + tolerance)
    ask_value = sum(float(u.get("ask_price") or 0) * float(u.get("ask_size") or 0)
                    for u in units if max(current * .995, ask) - tolerance <= float(u.get("ask_price") or 0) <= ask_ceiling + tolerance)
    return {"one_tick": one_tick, "coarse_tick": coarse, "spread_pct": spread,
            "tick_size": float(tick), "best_bid": bid, "best_ask": ask,
            "depth_supported": ratio >= .2 and min(bid_value, ask_value) >= 5_000_000,
            "nearby_depth_supported": bool(min(bid_value, ask_value) >= 5_000_000
                                            and ask_value > 0 and bid_value / ask_value >= .2),
            "bid_value_krw": bid_value, "ask_value_krw": ask_value}


def _tick_spread_context(samples, current, config, *, require_coarse=True):
    quotes = [_quote_execution_context(s, current, config) for s in samples]
    confirmed = bool(config.tick_spread_enabled and len(quotes) >= 3
        and all(q.get("one_tick") and q["spread_pct"] <= config.hard_max_spread_pct for q in quotes)
        and (not require_coarse or any(q.get("coarse_tick") for q in quotes))
        and sum(q["depth_supported"] for q in quotes) >= len(quotes) // 2 + 1)
    return {"confirmed": confirmed, "sample_count": len(quotes),
            "spread_pct": max((q["spread_pct"] for q in quotes), default=float("inf")),
            "tick_size": quotes[-1].get("tick_size") if quotes else None,
            "quotes": quotes}


def _flow_book_supported(samples, current, config):
    """Execution flow can replace imbalance, never a thin/wide/invalid book."""
    supported = 0
    for sample in samples:
        q = _quote_execution_context(sample, current, config)
        if ((q.get("nearby_depth_supported") and q["spread_pct"] <= config.max_spread_pct)
                or (q["depth_supported"] and q.get("coarse_tick"))):
            supported += 1
    return len(samples) >= 3 and supported >= len(samples) // 2 + 1


def validate_candidate_survival(
    candidate: dict[str, Any],
    ticker: dict[str, Any],
    orderbook_samples: list[dict[str, Any]],
    candles_1m: list[dict[str, Any]],
    config: CandidateConfig,
    *, as_of: datetime | None = None,
    execution_as_of: datetime | None = None,
    candles_5m: list[dict[str, Any]] | None = None,
    candles_15m: list[dict[str, Any]] | None = None,
    trade_flow_context: dict[str, Any] | None = None,
    candles_60m: list[dict[str, Any]] | None = None,
) -> tuple[dict[str, Any], list[str]]:
    """Recheck only the conditions that can invalidate an already-built setup.

    Relative rank and short-window momentum naturally cool after the initial
    impulse. Requiring the complete entry screen again turned the survival
    delay into a second discovery test and removed otherwise intact setups.
    """
    c1 = _completed(candles_1m, 1, as_of)
    if len(c1) < 21:
        return {}, ["생존 확인용 완료봉 데이터 부족"]

    current = float(ticker["trade_price"])
    breakout = float(candidate["breakout_level"])
    completed_close = float(c1[0]["trade_price"])
    volume_ratio, volume_previous = _volume_metrics(c1)
    book_ratio, spread, ratios = _book_metrics(orderbook_samples)
    execution_spread_context = _tick_spread_context(orderbook_samples, current, config, require_coarse=False)
    hard_book_persistent = sum(
        ratio >= config.hard_min_orderbook_ratio for ratio in ratios
    ) >= max(1, len(ratios) // 2 + 1)

    flow_lane = bool(candidate.get("trade_flow_leader"))
    flow_now = as_of or datetime.now(timezone.utc)
    execution_now = execution_as_of or flow_now
    execution_spread_context["as_of"] = flow_now.timestamp()
    survival_spread_ok = bool(spread <= config.max_spread_pct or
        (candidate.get("tick_spread_exception") and execution_spread_context["confirmed"]))
    continuation_lane = bool(flow_lane and candidate.get("flow_continuation_entry"))
    flow_evidence = (_completed_leader_context(candles_1m, candles_5m or [], candles_15m or [], flow_now)
                     if continuation_lane else _flow_entry_context(candles_1m, candles_5m or [], candles_15m or [], flow_now, config)) if flow_lane else {}
    flow_higher = higher_timeframe_context(candles_15m or [], candles_60m or [], [], now=flow_now) if flow_lane else {}
    flow_valid = bool(flow_lane and config.trade_flow_leader_enabled
        and buying_persistent(trade_flow_context, current, execution_now.timestamp(),
                              min_close_gain_pct=0.0 if continuation_lane else 0.1)
        and _flow_book_supported(orderbook_samples, current, config)
        and survival_spread_ok
        and flow_evidence.get("confirmed")
        and flow_higher.get("ready")
        and (flow_higher.get("hourly_established") or (continuation_lane and flow_higher["hourly"]["support_intact"]))
        and flow_higher.get("fifteen_intact") and flow_higher["fifteen"]["above_ma20"]
        and current >= max(breakout, float(flow_evidence.get("level") or breakout))
        and current <= float(flow_evidence.get("close") or current) * 1.005)
    if continuation_lane:
        flow_valid = bool(flow_valid and current >= float(flow_evidence.get("low") or breakout)
                          and completed_close >= max(breakout, float(flow_evidence.get("level") or breakout),
                                                     float(flow_evidence.get("low") or breakout))
                          and analyze_candles(c1)["rsi14"] < 95
                          and analyze_candles(completed_context_candles(candles_5m or [], 5, flow_now))["rsi14"] < 95)
    if flow_lane and not continuation_lane and candidate.get("double_bb_timeframe") == "5분":
        bars = completed_context_candles(candles_5m or [], 5, flow_now)
        wb = _double_bollinger_context(bars, breakout, lookback=config.double_bb_lookback,
            retest_tolerance_pct=config.retest_tolerance_pct, reversal_wick_ratio=config.double_bb_reversal_wick_ratio)
        anchor = float(wb.get("retest_level") or breakout)
        flow_valid = bool(flow_valid and wb.get("confirmed") and not wb.get("fake_breakout")
                          and current >= anchor and completed_close >= anchor)
    structure_lane = bool(candidate.get("completed_structure_entry"))
    structure_evidence = _completed_structure_context(candles_1m, candles_5m or [], candles_15m or [], flow_now, config) if structure_lane else {}
    structure_book = _structure_book_context(orderbook_samples, current, config, execution_now, trade_flow_context) if structure_lane else {}
    structure_higher = higher_timeframe_context(candles_15m or [], candles_60m or [], [], now=flow_now) if structure_lane else {}
    structure_valid = bool(structure_lane and config.completed_structure_enabled
        and structure_evidence.get("confirmed") and structure_book.get("confirmed")
        and structure_higher.get("ready") and structure_higher.get("fifteen_intact")
        and structure_higher["fifteen"]["above_ma20"] and structure_higher["hourly"]["above_ma20"]
        and structure_higher["hourly"]["support_intact"]
        and current >= max(breakout, float(structure_evidence.get("level") or 0))
        and completed_close >= max(breakout, float(structure_evidence.get("level") or 0))
        and current <= float(structure_evidence.get("close") or 0) * 1.005
        and analyze_candles(c1)["rsi14"] < 95
        and analyze_candles(completed_context_candles(candles_5m or [], 5, flow_now))["rsi14"] < 95)
    if structure_valid and (trade_flow_context or {}).get("ready"):
        flow = trade_flow_context
        if 0 <= execution_now.timestamp() - float(flow.get("as_of") or 0) <= 10:
            structure_valid = sum(float(w.get("sell_krw") or 0) for w in flow.get("windows", [])) <= 1.5 * sum(float(w.get("buy_krw") or 0) for w in flow.get("windows", []))
    rejected: list[str] = []
    if structure_lane and not structure_valid:
        rejected.append("완료 5분 구조·거래량·상위 추세·가까운 호가 생존 실패")
    if flow_lane and not flow_valid:
        rejected.append("체결 매수 지속·완료봉 돌파·호가 깊이 생존 실패")
    if current <= float(candidate["stop_price"]):
        rejected.append("계획 손절선 이하")
    if current >= float(candidate["chase_limit"]):
        rejected.append("추격금지선 도달")
    if completed_close < breakout or current < breakout * 0.998:
        rejected.append("돌파선 유지 실패")
    if any(current < float(level) * 0.998 or completed_close < float(level) * 0.998
           for level in candidate.get("cleared_resistance_levels", [])):
        rejected.append("돌파한 과거 저항의 지지 전환 유지 실패")
    explosive = candidate.get("selection_lane") == "explosive_leader"
    if explosive:
        context = _explosive_bar_context(candles_1m, candles_5m or [], candles_15m or [],
                                         as_of or datetime.now(timezone.utc))
        if not context.get("confirmed"):
            rejected.append("완료 5·15분 돌파 거래량·구조 생존 실패")
        elif current < float(context["level"]) or current > float(context["close"]) * 1.01:
            rejected.append("완료 5분 돌파선 이탈 또는 이격 과다")
    if not explosive and not flow_lane and not structure_lane and volume_ratio < config.availability_min_volume_ratio:
        rejected.append(f"완료 1분봉 거래량 붕괴({volume_ratio:.2f}배)")
    if not explosive and not flow_lane and not structure_lane and volume_previous < config.availability_min_volume_vs_previous:
        rejected.append(f"직전 봉 대비 거래량 급감({volume_previous:.2f}배)")
    if not hard_book_persistent and not flow_valid and not structure_valid:
        rejected.append(f"호가 지지 소멸({book_ratio:.2f}배)")
    if candidate.get("rsi_breakout_exception"):
        evidence = _explosive_bar_context(candles_1m, candles_5m or [], candles_15m or [],
                                          as_of or datetime.now(timezone.utc), impulse_min=1.5)
        normal_book = sum(x >= config.min_orderbook_ratio for x in ratios) >= max(1, len(ratios) // 2 + 1)
        if (not evidence.get("confirmed") or not normal_book
                or not survival_spread_ok
                or current < max(breakout, float(evidence.get("level") or breakout))
                or current > float(evidence.get("close") or current) * 1.005):
            rejected.append("RSI 예외 완료봉 돌파·호가 생존 실패")
    if candidate.get("completed_breakout_entry"):
        evidence = _explosive_bar_context(candles_1m, candles_5m or [], candles_15m or [],
                                          as_of or datetime.now(timezone.utc), impulse_min=1.5)
        normal_book = sum(x >= config.min_orderbook_ratio for x in ratios) >= max(1, len(ratios) // 2 + 1)
        if (not evidence.get("confirmed") or not normal_book
                or not survival_spread_ok
                or current < max(breakout, float(evidence.get("level") or breakout))
                or current > float(evidence.get("close") or current) * 1.005):
            rejected.append("눌림 대안 완료봉 돌파·거래량·호가 생존 실패")
    if candidate.get("completed_wb_retest_entry"):
        now = as_of or datetime.now(timezone.utc)
        bars = completed_context_candles(candles_5m or [], 5, now)
        wb = _double_bollinger_context(bars, breakout, lookback=config.double_bb_lookback,
            retest_tolerance_pct=config.retest_tolerance_pct, reversal_wick_ratio=config.double_bb_reversal_wick_ratio)
        evidence = _completed_retest_volume_context(candles_1m, candles_5m or [], candles_15m or [], now,
                                                    float(wb.get("retest_level") or breakout))
        normal_book = sum(x >= config.min_orderbook_ratio for x in ratios) >= max(1, len(ratios) // 2 + 1)
        if (not evidence.get("confirmed") or not wb.get("first_retest") or wb.get("fake_breakout")
                or not normal_book or not survival_spread_ok
                or completed_close < float(wb.get("retest_level") or breakout)
                or current < max(breakout, float(evidence.get("level") or breakout), float(wb.get("retest_level") or breakout))
                or current > float(evidence.get("close") or current) * 1.005):
            rejected.append("완료 5분 WB 첫 재지지·거래량·호가 생존 실패")
    if candidate.get("breadth_breakout_exception"):
        evidence = _explosive_bar_context(candles_1m, candles_5m or [], candles_15m or [],
                                         as_of or datetime.now(timezone.utc))
        if (not evidence.get("confirmed") or not hard_book_persistent
                or spread > config.max_spread_pct
                or current < max(breakout, float(evidence.get("level") or breakout))
                or current > float(evidence.get("close") or current) * 1.005):
            rejected.append("확산도 예외 완료봉 돌파·호가 생존 실패")
    if spread > config.hard_max_spread_pct:
        rejected.append(f"호가 스프레드 극단적 과다({spread:.2f}%)")

    survival_cost = {}
    if structure_lane:
        try:
            survival_cost = execution_cost_metrics(current, float(candidate["stop_price"]),
                float(candidate["target_1"]), float(candidate["target_2"]),
                candidate.get("exit_plan"), spread, config.tick_spread_cost_buffer_pct)
            if survival_cost["net_risk_reward"] < config.tick_spread_min_net_risk_reward:
                rejected.append("구조 돌파형 비용 차감 손익비 부족")
        except (KeyError, TypeError, ValueError):
            rejected.append("구조 돌파형 비용 계획 무효")
    if candidate.get("tick_spread_exception"):
        volume5 = _volume_metrics(completed_context_candles(candles_5m or [], 5, flow_now))[0]
        volume15 = _volume_metrics(completed_context_candles(candles_15m or [], 15, flow_now))[0]
        if (not execution_spread_context["confirmed"]
                or not buying_persistent(trade_flow_context, current, execution_now.timestamp(), min_close_gain_pct=0.0)
                or volume5 < 1.5 or volume15 < 1.0):
            rejected.append("최소 호가 단위 예외의 반복 호가·매수 체결·완료 거래량 생존 실패")
        try:
            survival_cost = execution_cost_metrics(current, float(candidate["stop_price"]),
                float(candidate["target_1"]), float(candidate["target_2"]), candidate.get("exit_plan"),
                spread, config.tick_spread_cost_buffer_pct)
            if survival_cost["net_risk_reward"] < config.tick_spread_min_net_risk_reward:
                rejected.append("최소 호가 단위 예외의 비용 차감 손익비 생존 실패")
        except (ValueError, KeyError, TypeError):
            rejected.append("최소 호가 단위 예외의 비용 계획 무효")

    return {
        **({"survival_structure_context": structure_evidence,
            "survival_structure_book_context": structure_book,
            "survival_structure_trade_flow": trade_flow_context,
            "completed_5m_volume_ratio": round(float(structure_evidence.get("volume_5m") or 0), 2),
            "completed_15m_volume_ratio": round(float(structure_evidence.get("volume_15m") or 0), 2)} if structure_lane else {}),
        **({"completed_5m_volume_ratio": round(float(flow_evidence.get("volume_5m") or 0), 2),
            "completed_15m_volume_ratio": round(float(flow_evidence.get("volume_15m") or 0), 2),
            "flow_volume_mode": flow_evidence.get("volume_mode", "standard"),
            "flow_execution_override": bool(candidate.get("flow_execution_override") or book_ratio < .2
                 or flow_evidence.get("volume_mode", "standard") != "standard")} if flow_lane else {}),
        "survival_flow_entry_context": flow_evidence if flow_lane and not continuation_lane else None,
        **({"completed_5m_volume_ratio": round(volume5, 2),
            "completed_15m_volume_ratio": round(volume15, 2)} if candidate.get("tick_spread_exception") else {}),
        "survival_execution_spread_context": execution_spread_context if candidate.get("tick_spread_exception") else None,
        "survival_execution_cost": survival_cost if candidate.get("tick_spread_exception") or structure_lane else None,
        "survival_trade_flow": trade_flow_context if flow_lane or candidate.get("tick_spread_exception") else None,
        "survival_continuation_context": flow_evidence if continuation_lane else None,
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
    candles_60m: list[dict[str, Any]] | None = None,
    candles_240m: list[dict[str, Any]] | None = None,
    btc_ticker: dict[str, Any] | None = None,
    btc_candles_5m: list[dict[str, Any]] | None = None,
    btc_candles_15m: list[dict[str, Any]] | None = None,
    orderbook_samples: list[dict[str, Any]] | None = None,
    as_of: datetime | None = None,
    execution_as_of: datetime | None = None,
) -> tuple[dict[str, Any] | None, list[str]]:
    """Evaluate a setup with persistent snapshots and lane-specific candles."""
    alert.pop("risk_plan_diagnostics", None)
    alert.pop("screening_metrics", None)
    context_now = (as_of or datetime.now(timezone.utc)).astimezone(timezone.utc)
    execution_now = (execution_as_of or context_now).astimezone(timezone.utc)
    if alert.get("signal") not in {
        "price_volume_surge",
        "breakout",
        "consolidation_rebreakout",
        "leader_volume_acceleration",
        "persistent_leader_acceleration",
    }:
        return None, ["상승 후보 신호가 아님"]
    candles_15m = candles_15m or candles_5m
    btc_candles_5m = btc_candles_5m or candles_5m
    btc_candles_15m = btc_candles_15m or btc_candles_5m
    btc_ticker = btc_ticker or {"signed_change_rate": 0.0}
    c1, c5, c15 = (
        _completed(candles_1m, 1, context_now),
        _completed(candles_5m, 5, context_now),
        _completed(candles_15m, 15, context_now),
    )
    b5, b15 = _completed(btc_candles_5m, 5, context_now), _completed(btc_candles_15m, 15, context_now)
    # MA60 is retained for the entry timeframes; 15m/BTC need 21 bars for
    # MA20, its slope and a latest-volume / previous-20 comparison. Missing
    # optional MA60 must not impose an unrelated 15-hour listing embargo.
    counts = dict(zip(("1m", "5m", "15m", "btc5m", "btc15m"),
                      map(len, (c1, c5, c15, b5, b15))))
    minimums = {"1m": 60, "5m": 60, "15m": 21, "btc5m": 21, "btc15m": 21}
    alert["screening_metrics"] = {
        "completed_candle_counts": counts, "required_candle_counts": minimums,
        "history_ready": all(counts[k] >= v for k, v in minimums.items()),
    }
    if not alert["screening_metrics"]["history_ready"]:
        missing = ", ".join(f"{k} {counts[k]}/{v}" for k, v in minimums.items()
                            if counts[k] < v)
        return None, [f"완료봉 데이터 부족({missing})"]

    current, signal_price = float(ticker["trade_price"]), float(alert["price"])
    day_change = float(ticker.get("signed_change_rate", 0)) * 100
    day_high = max(current, float(ticker.get("high_price") or current))
    drawdown_from_day_high_pct = max(0.0, (1 - current / day_high) * 100)
    # A fresh completed breakout admitted from an existing watch is a new
    # setup, not a re-entry into the old impulse. Keep its independent price
    # structure and risk checks, but do not carry the old day-high drawdown
    # penalty into the new setup. Actual pullback/retake checks stay reentries.
    fresh_breakout_recheck = bool(
        alert.get("fresh_breakout_recheck")
        or alert.get("completed_breakout_recheck")
    )
    origin_signal_price = float(alert.get("origin_signal_price") or 0.0)
    fresh_breakout_extension_pct = (
        (current / origin_signal_price - 1) * 100 if origin_signal_price > 0 else 0.0
    )
    is_reentry_path = bool(
        alert.get("is_reentry")
        or alert.get("leader_pullback_recheck")
        or (alert.get("watchlist_recheck") and not fresh_breakout_recheck)
    )
    trade_value_24h = float(
        ticker.get("acc_trade_price_24h") or config.min_trade_value_24h_krw
    )
    one, five, fifteen = analyze_candles(c1), analyze_candles(c5), analyze_candles(c15)
    btc_five, btc_fifteen = analyze_candles(b5), analyze_candles(b15)
    rsi1, rsi5 = float(one.get("rsi14") or 0), float(five.get("rsi14") or 0)
    extension = (current / signal_price - 1) * 100
    volume_ratio, volume_previous = _volume_metrics(c1)
    close_position, upper_wick = _candle_shape(c1[0])
    one_volume_ratio = volume_ratio
    samples = orderbook_samples or [orderbook]
    book_ratio, spread, ratios = _book_metrics(samples)
    execution_spread_context = _tick_spread_context(samples, current, config)
    execution_spread_context["as_of"] = context_now.timestamp()
    tick_spread_ready = bool(execution_spread_context["confirmed"]
        and buying_persistent(alert.get("trade_flow_context"), current, execution_now.timestamp(), min_close_gain_pct=0.0)
        and _volume_metrics(c5)[0] >= 1.5 and _volume_metrics(c15)[0] >= 1.0)
    execution_spread_ok = spread <= config.max_spread_pct or tick_spread_ready
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
    btc_bar_change = (
        float(b5[0]["trade_price"]) / float(b5[0]["opening_price"]) - 1
    ) * 100
    btc_window_change = (
        float(b5[0]["trade_price"]) / float(b5[3]["trade_price"]) - 1
    ) * 100
    btc_crash = (
        btc_bar_change <= config.btc_crash_5m_pct
        or btc_window_change <= config.btc_crash_15m_pct
    )
    higher = (
        higher_timeframe_context(candles_15m, candles_60m or [], candles_240m or [], now=context_now)
        if config.higher_timeframe_enabled
        else {"ready": False, "status": "비활성"}
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
    double_bb = _double_bollinger_context(
        c1,
        breakout,
        lookback=(
            max(config.double_bb_lookback, 12)
            if alert.get("signal") == "persistent_leader_acceleration"
            else config.double_bb_lookback
        ),
        retest_tolerance_pct=config.retest_tolerance_pct,
        reversal_wick_ratio=config.double_bb_reversal_wick_ratio,
    )
    double_bb_one = double_bb
    wb_ready = bool(config.double_bb_enabled and double_bb.get("ready"))
    wb_confirmed = bool(wb_ready and double_bb.get("confirmed"))
    wb_fake_breakout = bool(wb_ready and double_bb.get("fake_breakout"))
    fresh_five = completed_context_candles(candles_5m, 5, context_now)
    fresh_fifteen = completed_context_candles(candles_15m, 15, context_now)
    sustained_retest = bool(
        alert.get("hourly_recheck")
        and relative_ready
        and relative_percentile <= config.early_leader_max_percentile
        and momentum_5m >= 0.2
        and momentum_15m is not None
        and momentum_15m >= 0.8
        and float(alert.get("momentum_60m_pct") or 0) >= 1.5
        and higher.get("ready")
        and higher.get("hourly_established")
        and higher.get("fifteen_intact")
        and higher["fifteen"]["above_ma20"]
        and _volume_metrics(fresh_five)[0] >= 1.5
        and _volume_metrics(fresh_fifteen)[0] >= 1.0
        and _trade_value_metrics(fresh_five)[0] >= 1.5
        and _trade_value_metrics(fresh_fifteen)[0] >= 1.0
        and recent_minute_candles_contiguous(candles_1m, context_now)
        and retest_confirmed
        and wb_confirmed
    )
    hourly_trend_core = bool(
        relative_ready
        and (relative_eligible or sustained_retest)
        and relative_percentile <= config.early_leader_max_percentile
        and higher.get("ready")
        and higher.get("hourly_established")
        and higher.get("fifteen_intact")
        and higher["fifteen"]["above_ma20"]
        and (retest_confirmed or wb_confirmed)
    )
    # Broaden the fast lane for exceptionally strong preleaders. A rank in the
    # top 3%, fresh 5m/15m strength and large 10m/30m turnover expansion can
    # qualify even when the market regime or the ordinary 5m threshold lags.
    # Hard execution, BTC-crash, structure and stop-risk checks still apply.
    narrow_market_fast_core = bool(
        config.fast_leader_enabled
        and alert.get("fast_leader")
        and alert.get("signal") == "leader_volume_acceleration"
        and relative_ready and relative_eligible
        and relative_percentile <= 3.0
        and early_trend
        and momentum_5m >= 1.0
        and momentum_15m is not None and momentum_15m > 0
        and preleader_ratio_10m >= 5.0
        and preleader_ratio_30m >= 3.0
        and float(alert.get("market_breadth_5m_pct") or 0.0) >= 20.0
        and _volume_metrics(fresh_five)[0] >= 1.5
        and _volume_metrics(fresh_fifteen)[0] >= 1.0
        and current >= breakout * 0.998
        and current >= float(five["ma20"])
        and extension <= config.fast_leader_max_extension_pct
        and hard_book_persistent
        and spread <= config.hard_max_spread_pct
        and not wb_fake_breakout
        and not btc_crash
        and btc_change > config.max_btc_decline_pct
    )
    fast_leader_core = bool(
        config.fast_leader_enabled
        and alert.get("fast_leader")
        and alert.get("signal") == "leader_volume_acceleration"
        and relative_ready and relative_eligible
        and relative_percentile <= config.fast_leader_max_percentile
        and early_trend
        and momentum_5m >= config.relative_strength_min_5m_pct
        and (momentum_15m is None or momentum_15m > 0)
        and preleader_ratio_10m >= config.fast_leader_min_value_ratio_10m
        and preleader_ratio_30m >= config.fast_leader_min_value_ratio_30m
        and _volume_metrics(fresh_five)[0] >= 1.5
        and _volume_metrics(fresh_fifteen)[0] >= 1.0
    ) or narrow_market_fast_core
    persistent_leader_core = bool(
        alert.get("signal") == "persistent_leader_acceleration"
        and alert.get("persistent_leader")
        and relative_ready
        and relative_percentile <= config.early_leader_max_percentile
        and momentum_5m >= config.relative_strength_min_5m_pct
        and momentum_15m is not None
        and momentum_15m >= config.persistent_leader_min_15m_pct
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
    # A small number of genuine leaders stay overbought throughout a clean
    # first pullback. Let that exact setup proceed to the normal 60s survival
    # check when rank, multi-timeframe momentum, completed volume, book depth,
    # and breakout hold all remain strong. This does not waive the 1m RSI cap,
    # resistance/R:R checks, or the configured survival/dispatch validation.
    elite_leader_retest = bool(
        alert.get("leader_pullback_recheck")
        and retest_confirmed
        and relative_ready
        and relative_eligible
        and leadership_persisted
        and relative_percentile <= config.risk_off_exception_max_percentile
        and early_trend
        and momentum_5m >= max(
            config.relative_strength_min_5m_pct,
            config.risk_off_exception_min_5m_pct,
        )
        and momentum_15m is not None
        and momentum_15m >= config.persistent_leader_min_15m_pct
        and current >= breakout * 0.998
        and current >= float(five["ma20"])
        and extension <= config.max_price_extension_pct
        and volume_ratio >= config.availability_min_volume_ratio
        and volume_previous >= config.availability_min_volume_vs_previous
        and hard_book_persistent
        and spread <= config.hard_max_spread_pct
        and day_change <= config.max_day_change_pct
        and rsi1 <= config.hard_max_rsi_1m
        and rsi5 <= config.elite_retest_max_rsi_5m
        and not wb_fake_breakout
    )

    explosive_context = _explosive_bar_context(candles_1m, candles_5m, candles_15m, context_now)
    explosive_core = bool(
        config.explosive_leader_enabled and explosive_context.get("confirmed")
        and relative_ready and relative_eligible and relative_percentile <= 2.0
        and momentum_5m >= 1.5 and momentum_15m is not None and momentum_15m > 0
        and current >= max(breakout, float(explosive_context["level"]))
        and current <= float(explosive_context["close"]) * 1.01
        and hard_book_persistent and spread <= config.hard_max_spread_pct
        and not btc_crash
    )
    # A narrower RSI-only exception on the ordinary lane. This does not waive
    # WB, one-minute quality, score, liquidity, breadth, or risk-plan gates.
    rsi_context = _explosive_bar_context(candles_1m, candles_5m, candles_15m,
                                        context_now, impulse_min=1.5)
    double_bb_five = _double_bollinger_context(
        c5, breakout, lookback=config.double_bb_lookback,
        retest_tolerance_pct=config.retest_tolerance_pct,
        reversal_wick_ratio=config.double_bb_reversal_wick_ratio,
    )
    wb_five_breakout = bool(config.double_bb_enabled
                            and double_bb_five.get("true_breakout")
                            and not double_bb_five.get("fake_breakout"))
    wb_five_retest = bool(config.double_bb_enabled and double_bb_five.get("first_retest")
                         and not double_bb_five.get("fake_breakout"))
    wb_retest_volume = _completed_retest_volume_context(candles_1m, candles_5m, candles_15m, context_now,
                                                       float(double_bb_five.get("retest_level") or breakout))
    completed_wb_retest_entry = bool(
        wb_five_retest and not wb_confirmed and not explosive_core and wb_retest_volume.get("confirmed")
        and not wb_fake_breakout and relative_ready and relative_eligible
        and relative_percentile <= config.early_leader_max_percentile
        and momentum_5m >= config.relative_strength_min_5m_pct
        and momentum_15m is not None and momentum_15m > 0
        and current >= max(breakout, float(double_bb_five["retest_level"]))
        and completed_close >= float(double_bb_five["retest_level"])
        and current <= float(wb_retest_volume["close"]) * 1.005
        and book_persistent and execution_spread_ok
        and not btc_crash and btc_change > config.max_btc_decline_pct)
    # A clean, completed volume breakout is an alternative to a first retest,
    # not evidence that a retest happened. Keep every ordinary quality, score,
    # risk and 60s survival check on this route.
    completed_breakout_entry = bool(
        rsi_context.get("confirmed") and not rsi_context.get("retest")
        and (wb_confirmed or wb_five_breakout) and not wb_fake_breakout
        and relative_ready and relative_eligible and early_trend
        and relative_percentile <= config.early_leader_max_percentile
        and momentum_5m >= config.relative_strength_min_5m_pct
        and momentum_15m is not None and momentum_15m > 0
        and current >= max(breakout, float(rsi_context["level"]))
        and current <= float(rsi_context["close"]) * 1.005
        and book_persistent and execution_spread_ok
        and not btc_crash and btc_change > config.max_btc_decline_pct
    )
    flow_context = alert.get("trade_flow_context") or {}
    flow_entry_context = _flow_entry_context(candles_1m, candles_5m, candles_15m, context_now, config)
    continuation_context = _completed_leader_context(candles_1m, candles_5m, candles_15m, context_now)
    flow_continuation_entry = bool(config.trade_flow_leader_enabled and config.double_bb_enabled
        and continuation_context.get("confirmed")
        and buying_persistent(flow_context, current, execution_now.timestamp(), min_close_gain_pct=0.0)
        and relative_ready and relative_percentile <= 2.0
        and momentum_5m >= .2 and momentum_15m is not None and momentum_15m >= .8
        and float(alert.get("momentum_60m_pct") or 0) >= 1.5
        and higher.get("ready") and higher.get("fifteen_intact")
        and higher["fifteen"]["above_ma20"] and higher["hourly"]["support_intact"]
        and float(alert.get("market_breadth_5m_pct") or 0) >= 20.0
        and _flow_book_supported(samples, current, config)
        and execution_spread_ok
        and current >= max(breakout, float(continuation_context["level"]), float(continuation_context["low"]))
        and completed_close >= max(breakout, float(continuation_context["level"]), float(continuation_context["low"]))
        and current <= float(continuation_context["close"]) * 1.005
        and not wb_fake_breakout and rsi1 < 95 and rsi5 < 95
        and not btc_crash and btc_change > config.max_btc_decline_pct)
    flow_leader = bool(config.trade_flow_leader_enabled
        and buying_persistent(flow_context, current, execution_now.timestamp())
        and flow_entry_context.get("confirmed")
        and (wb_confirmed or wb_five_breakout or wb_five_retest) and not wb_fake_breakout
        and (wb_confirmed or current >= float(double_bb_five.get("retest_level") or breakout))
        and (wb_confirmed or completed_close >= float(double_bb_five.get("retest_level") or breakout))
        and relative_ready and relative_percentile <= 2.0
        and momentum_5m >= .2 and momentum_15m is not None and momentum_15m >= .8
        and float(alert.get("momentum_60m_pct") or 0) >= 1.5
        and higher.get("ready") and higher.get("hourly_established")
        and higher.get("fifteen_intact") and higher["fifteen"]["above_ma20"]
        and float(alert.get("market_breadth_5m_pct") or 0) >= 20.0
        and _flow_book_supported(samples, current, config)
        and execution_spread_ok
        and current >= max(breakout, float(flow_entry_context["level"]))
        and current <= float(flow_entry_context["close"]) * 1.005
        and not btc_crash and btc_change > config.max_btc_decline_pct) or flow_continuation_entry
    # Keep the flow route distinct: it requires full 60s survival and does not
    # inherit fast/RSI/breadth exceptions with different revalidation rules.
    if flow_leader:
        fast_leader_core = explosive_core = completed_breakout_entry = completed_wb_retest_entry = False
        hourly_trend_core = True
        rsi_context = flow_entry_context
    if flow_continuation_entry:
        rsi_context = continuation_context
    structure_context = _completed_structure_context(candles_1m, candles_5m, candles_15m, context_now, config)
    structure_book = _structure_book_context(samples, current, config, execution_now, flow_context)
    known_selling = bool(flow_context.get("ready")
        and 0 <= execution_now.timestamp() - float(flow_context.get("as_of") or 0) <= 10
        and sum(float(w.get("sell_krw") or 0) for w in flow_context.get("windows", []))
        > 1.5 * sum(float(w.get("buy_krw") or 0) for w in flow_context.get("windows", [])))
    structure_checks = {
        "enabled": bool(config.completed_structure_enabled and config.double_bb_enabled),
        "completed_breakout_volume": bool(structure_context.get("confirmed")),
        "local_execution_book": bool(structure_book.get("confirmed")),
        "relative_strength": bool(relative_ready and relative_percentile <= 2),
        "relative_momentum": bool(momentum_5m >= .2 and momentum_15m is not None
            and momentum_15m >= .8 and float(alert.get("momentum_60m_pct") or 0) >= 1.5),
        "completed_higher_trend": bool(higher.get("ready") and higher.get("fifteen_intact")
            and higher["fifteen"]["above_ma20"] and higher["hourly"]["above_ma20"]
            and higher["hourly"]["support_intact"]),
        "breadth": float(alert.get("market_breadth_5m_pct") or 0) >= 20,
        "level_hold": bool(current >= max(breakout, float(structure_context.get("level") or 0))
            and completed_close >= max(breakout, float(structure_context.get("level") or 0))),
        "chase": (current <= float(structure_context["close"]) * 1.005
                  if structure_context.get("close") else None),
        "btc": bool(not btc_crash and btc_change > config.max_btc_decline_pct),
        "selling_and_heat": bool(not known_selling and rsi1 < 95 and rsi5 < 95),
    }
    completed_structure_entry = bool(not flow_leader and all(structure_checks.values()))
    if completed_structure_entry:
        # A new completed breakout creates a new plan. Never move an existing
        # candidate's stop, or measure today's entry against an hours-old alert.
        fast_leader_core = explosive_core = completed_breakout_entry = completed_wb_retest_entry = False
        narrow_market_fast_core = False
        breakout = max(breakout, float(structure_context["level"]))
        signal_price = float(structure_context["close"])
        extension = (current / signal_price - 1) * 100
        rsi_context = structure_context
        hourly_trend_core = True
        retest_confirmed = bool(structure_context.get("retest"))
    if completed_wb_retest_entry:
        # Keep 1m execution quality, but use a 5m structural initial stop.
        fast_leader_core = False
        rsi_context = wb_retest_volume
    wb_confirmation_timeframe = "1분"
    if (completed_structure_entry and structure_context.get("wb_confirmed")) or completed_wb_retest_entry or ((completed_breakout_entry or flow_leader)
            and not wb_confirmed and (wb_five_breakout or wb_five_retest)):
        # A completed 5m WB breakout can confirm the structural route even
        # when the short 1m bands no longer signal. Keep 1m hold/quality gates.
        double_bb = double_bb_five
        wb_ready = wb_confirmed = True
        wb_confirmation_timeframe = "5분"
    if flow_continuation_entry:
        # Historical WB clearance is evidence of continuation, never a claim
        # that the current candle broke both bands or tested the old anchor.
        double_bb = {**double_bb_five, "confirmed": True, "fake_breakout": False,
                     "true_breakout": False, "first_retest": False,
                     "status": "WB 완료 돌파 후 저점 상승·매수 체결 지속"}
        wb_ready = wb_confirmed = True
        wb_confirmation_timeframe = "5분"
    rsi_breakout_exception = bool(
        config.explosive_leader_enabled and not explosive_core and not flow_leader and not completed_structure_entry and not completed_wb_retest_entry
        and (rsi1 > config.hard_max_rsi_1m or rsi5 > config.hard_max_rsi_5m)
        and rsi_context.get("confirmed") and wb_confirmed and not wb_fake_breakout
        and relative_ready and relative_eligible and relative_percentile <= 2.0
        and momentum_5m >= 1.5 and momentum_15m is not None and momentum_15m > 0
        and current >= max(breakout, float(rsi_context["level"]))
        and current <= float(rsi_context["close"]) * 1.005
        and book_persistent and spread <= config.max_spread_pct
        and not btc_crash and btc_change > config.max_btc_decline_pct
    )
    # Retain real 1m RSI for risk deductions, but use the completed 5m candle
    # for volume/shape gates in this narrowly qualified lane.
    if explosive_core or flow_leader or completed_structure_entry:
        quality_context = rsi_context if flow_leader or completed_structure_entry else explosive_context
        volume_ratio = float(quality_context["volume_5m"])
        volume_previous = float(quality_context["volume_previous_5m"])
        close_position = float(quality_context["close_position"])
        upper_wick = float(quality_context["upper_wick"])

    rejected: list[str] = []
    if fresh_breakout_recheck and fresh_breakout_extension_pct > 5.0:
        rejected.append(
            "초기 포착가 대비 5% 초과 상승으로 신규 진입 제한"
            f"(+{fresh_breakout_extension_pct:.1f}%)"
        )
    soft_warnings: list[str] = []
    if (config.completed_structure_enabled and structure_context.get("confirmed")
            and not completed_structure_entry and not flow_leader):
        labels = {
            "local_execution_book": "가까운 반복 호가·비용 검증 대상 미달",
            "relative_strength": "상대강도", "relative_momentum": "15·60분 상대 추세",
            "completed_higher_trend": "완료 15·60분 추세",
            "breadth": "시장 확산도", "level_hold": "돌파선 유지",
            "chase": "실행 가격 이격", "btc": "BTC 보호",
            "selling_and_heat": "실제 매도 우위 또는 극단 과열",
        }
        blockers = [labels.get(k, k) for k, v in structure_checks.items() if not v]
        rejected.append("완료 5분 돌파 확인 후 실행조건 미충족(" + "·".join(blockers) + ")")
    if (
        is_reentry_path
        and drawdown_from_day_high_pct
        > config.reentry_max_drawdown_from_day_high_pct
    ):
        rejected.append(
            "재진입 후보 당일 고점 대비 낙폭 과다"
            f"(-{drawdown_from_day_high_pct:.1f}% > "
            f"{config.reentry_max_drawdown_from_day_high_pct:.1f}%)"
        )
    if relative_ready and config.relative_strength_required:
        if relative_percentile > config.relative_strength_top_percent:
            rejected.append(
                "전체 시장 상대강도 상위권 아님"
                f"({relative_percentile:.1f}백분위)"
            )
        elif (
            not relative_eligible and not flow_leader and not completed_structure_entry
            and momentum_5m >= config.relative_strength_min_5m_pct
            and (momentum_15m is None or momentum_15m > 0)
        ):
            rejected.append("상대강도 자격 재확인 필요(순위 외 조건 불일치)")
        if momentum_5m < config.relative_strength_min_5m_pct and not sustained_retest and not flow_leader and not completed_structure_entry:
            rejected.append(f"5분 상대 모멘텀 부족({momentum_5m:+.2f}%)")
        if momentum_15m is not None and momentum_15m <= 0:
            rejected.append(f"15분 추세 미확인({momentum_15m:+.2f}%)")
        if (
            config.early_trend_required
            and not early_trend
            and not persistent_leader_core
            and not hourly_trend_core
            and not explosive_core
            and not completed_wb_retest_entry
            and not completed_structure_entry
        ):
            rejected.append("상승 초기 가속 구간 아님")
        if (
            config.require_first_retest
            and not retest_confirmed
            and not fast_leader_core
            and not persistent_leader_core
            and not explosive_core
            and not completed_breakout_entry
            and not flow_leader
            and not completed_wb_retest_entry
            and not completed_structure_entry
        ):
            rejected.append("첫 눌림·돌파선 재지지 미확인")
    confirmation_started_at = alert.get("confirmation_started_at_utc") or alert.get(
        "time_utc"
    )
    if not fast_leader_core and not completed_structure_entry and not _completed_after_signal(
        c1[0], confirmation_started_at
    ):
        rejected.append("신호 이후 확인시간 20초를 채운 완료 1분봉 없음")
    if not fast_leader_core and completed_close < breakout:
        rejected.append("완료 1분봉이 돌파선 아래 마감")
    if current < breakout * 0.998:
        rejected.append("돌파선 재지지 실패")
    if wb_fake_breakout and not explosive_core and not completed_structure_entry:
        rejected.append("WB 상단 접촉 후 밴드 복귀·긴 윗꼬리(가짜 돌파)")
    if (
        config.double_bb_enabled
        and config.double_bb_require_confirmation
        and wb_ready
        and not wb_confirmed
        and not fast_leader_core
        and not elite_leader_retest
        and not explosive_core
        and not completed_structure_entry
    ):
        wb_rejection_label = ("완료 5분 WB 구조 확인·진입 실행조건 미충족"
                              if wb_five_breakout or wb_five_retest or continuation_context.get("confirmed")
                              else "WB 두 상단·직전 매물대 동시 돌파 또는 첫 재지지 미확인")
        rejected.append(
            wb_rejection_label +
            f"(1분 {double_bb.get('status', '데이터 부족')} · "
            f"5분 {double_bb_five.get('status', '데이터 부족')} · "
            "완료봉 대체 경로 실행조건 미충족)"
        )
    allowed_day_change = (
        config.persistent_leader_max_day_change_pct
        if persistent_leader_core or narrow_market_fast_core
        else config.max_day_change_pct
    )
    day_overheated = day_change > allowed_day_change
    elevated_risk = day_change >= 10.0 or rsi1 >= 70.0 or rsi5 >= 68.0
    early_leader_lane = bool(
        (
            early_leader_core
            or fast_leader_core
            or persistent_leader_core
            or hourly_trend_core
            or explosive_core
        )
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
    early_leader_lane = early_leader_lane or explosive_core or flow_leader or completed_wb_retest_entry or completed_structure_entry
    # Availability balancing is allowed to collect mild misses first. Elevated
    # setups are narrowed again below to candle-shape warnings only, so volume,
    # trend and resistance safety floors remain strict.
    strict_quality = not config.availability_balance_enabled
    if not explosive_core and not rsi_breakout_exception and not flow_continuation_entry and not completed_structure_entry and (rsi1 > config.hard_max_rsi_1m or (
        rsi5 > config.hard_max_rsi_5m and not elite_leader_retest
    )):
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
    elif current < float(one["ma20"]) and not completed_structure_entry:
        message = "완료봉 기준 1분 20이평 아래"
        (rejected if strict_quality else soft_warnings).append(message)

    contracting_flow_retest = bool(flow_leader and rsi_context.get("volume_mode") == "contracting_first_retest")
    contracting_structure_retest = bool(completed_structure_entry
        and structure_context.get("structure_mode") == "first_retest")
    # This lane already verifies >=0.5x current activity, contraction from a
    # >=1.5x completed impulse, 15m activity and ten uninterrupted minutes.
    # Requiring another impulse here contradicts a qualified first pullback.
    if not fast_leader_core and not contracting_flow_retest and not contracting_structure_retest and volume_ratio < config.availability_min_volume_ratio:
        rejected.append(f"완료 1분봉 거래량 절대 부족({volume_ratio:.2f}배)")
    elif not fast_leader_core and not contracting_flow_retest and not contracting_structure_retest and volume_ratio < config.min_completed_volume_ratio:
        message = f"완료 1분봉 거래량 다소 부족({volume_ratio:.2f}배)"
        (rejected if strict_quality else soft_warnings).append(message)

    if (
        not fast_leader_core and not explosive_core and not flow_leader and not completed_structure_entry
        and volume_previous < config.availability_min_volume_vs_previous
    ):
        rejected.append(f"직전 봉 대비 거래량 급감({volume_previous:.2f}배)")
    elif not fast_leader_core and not explosive_core and not flow_leader and not completed_structure_entry and volume_previous < config.min_volume_vs_previous:
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

    # Below an MA is a caution, not proof of a crash. A real completed-bar
    # crash cannot be rescued by a strong orderbook or a high condition score.
    btc_weak = btc_change <= config.max_btc_decline_pct or btc_crash
    btc_caution = btc_bearish and not btc_weak
    if btc_crash:
        rejected.append(
            f"BTC 완료봉 급락(5분 {btc_bar_change:+.2f}%, 15분 {btc_window_change:+.2f}%)"
        )
    market_regime = str(alert.get("market_regime") or "neutral")
    market_breadth_5m_pct = float(alert.get("market_breadth_5m_pct") or 0.0)

    # Accuracy-first gates. These combinations produced high condition scores
    # despite poor follow-through in production. They are direct contradictions
    # to an actionable long entry and cannot be offset by setup/volume points.
    breadth_softened = False
    # Old reentries keep the strict exception because a former leader can
    # still rank well while distribution continues. A genuinely fresh leader
    # gets a separate lane, then must still pass every normal safety and
    # survival check before Telegram dispatch.
    risk_off_reentry_exception = bool(
        is_reentry_path
        and relative_ready
        and relative_eligible
        and relative_percentile <= config.risk_off_exception_max_percentile
        and momentum_5m >= config.risk_off_exception_min_5m_pct
        and momentum_15m is not None
        and momentum_15m > 0
    )
    risk_off_fresh_leader_exception = bool(
        not is_reentry_path
        and (
            narrow_market_fast_core
            or (
                relative_ready
                and relative_eligible
                and relative_percentile <= config.risk_off_fresh_leader_max_percentile
                and momentum_5m >= config.risk_off_fresh_leader_min_5m_pct
                and momentum_15m is not None and momentum_15m > 0
                and early_trend
                and (early_leader_core or persistent_leader_core)
                and volume_ratio >= config.min_completed_volume_ratio
                and volume_previous >= config.min_volume_vs_previous
            )
        )
    )
    # A top leader with fresh, completed structural evidence can rotate in a
    # moderately weak market even after rolling 5m momentum cools below 1.5%.
    # This exempts ONLY breadth. It cannot waive WB, RSI, book, chase, stop,
    # score or any other ordinary-lane rule (including failed retests).
    breadth_breakout_exception = bool(
        config.explosive_leader_enabled and not flow_leader and not completed_structure_entry
        and market_regime == "risk_off"
        and 20.0 <= market_breadth_5m_pct < config.hard_min_market_breadth_pct
        and explosive_context.get("confirmed")
        and wb_confirmed and not wb_fake_breakout
        and relative_ready and relative_eligible and relative_percentile <= 2.0
        and momentum_5m >= config.relative_strength_min_5m_pct
        and momentum_15m is not None and momentum_15m > 0
        and (alert.get("momentum_60m_pct") is None or float(alert["momentum_60m_pct"]) > 0)
        and current >= max(breakout, float(explosive_context["level"]))
        and current <= float(explosive_context["close"]) * 1.005
        and hard_book_persistent and spread <= config.max_spread_pct
        and not btc_weak
    )
    risk_off_exception = bool(
        risk_off_reentry_exception or risk_off_fresh_leader_exception or explosive_core
        or breadth_breakout_exception or flow_leader or completed_structure_entry
    )
    if (
        market_regime == "risk_off"
        and market_breadth_5m_pct < config.hard_min_market_breadth_pct
    ):
        if risk_off_exception:
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
        if (early_leader_lane and hard_book_persistent) or flow_leader or completed_structure_entry:
            book_softened = True
        else:
            rejected.append(f"호가 지지 하드차단({label}, {book_ratio:.2f}배)")
    if btc_weak and book_ratio < config.btc_weak_min_orderbook_ratio:
        rejected.append(
            "BTC 약세·호가 약세 동시 발생"
            f"({btc_change:+.2f}%, {book_ratio:.2f}배)"
        )
    hot_book_floor = config.min_orderbook_ratio if fast_leader_core else 1.0
    if not explosive_core and not flow_leader and not completed_structure_entry and rsi1 >= config.hot_rsi_1m and book_ratio < hot_book_floor:
        rejected.append(
            f"단기 과열·호가 약세 동시 발생(RSI {rsi1:.1f}, {book_ratio:.2f}배)"
        )
    tick_spread_exception = bool(tick_spread_ready and
        (completed_wb_retest_entry or completed_breakout_entry or flow_leader))
    flow_execution_override = bool(flow_leader and (book_ratio < .2
        or rsi_context.get("volume_mode", "standard") != "standard"))
    spread_softened = bool(tick_spread_exception or completed_structure_entry or (
        early_leader_lane
        and config.max_spread_pct < spread <= config.hard_max_spread_pct
    ))
    if spread > config.max_spread_pct and not spread_softened:
        rejected.append(f"호가 스프레드 허용치 초과({spread:.2f}%)")

    resistance_levels = (
        _resistances(current, ticker, c5, c15, completed_impulse_high=float(c5[0]["high_price"]))
        if explosive_core or rsi_breakout_exception or flow_leader or completed_structure_entry
        else _resistances(current, ticker, c5, c15)
    )
    cleared_resistance_levels = []
    if explosive_core or rsi_breakout_exception or flow_leader or completed_structure_entry:
        proof = explosive_context if explosive_core else rsi_context
        # A completed close must clear the old zone; the live and completed
        # one-minute prices must still hold its 0.2% support tolerance.
        cleared_resistance_levels = [level for level in resistance_levels
            if float(proof["close"]) > level * 1.001
            and current >= level * 0.998 and completed_close >= level * 0.998]
        resistance_levels = [level for level in resistance_levels
                             if level not in cleared_resistance_levels]
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
    # For a independently qualified 5m breakout, a real close resistance is
    # still the target ceiling. Decide using its tick-rounded first target,
    # structural stop and net costs below, not distance alone. Never extend it.
    structure_resistance_pending = bool(completed_structure_entry
        and resistance_confirmed and room > 0)
    if resistance_confirmed and room < config.hard_min_resistance_room_pct:
        leader_resistance_override = bool(
            not completed_structure_entry and early_leader_lane
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
            not completed_structure_entry and config.availability_balance_enabled
            and room >= config.availability_resistance_floor_pct
            and alert.get("signal") == "consolidation_rebreakout"
            and not elevated_risk
            and not btc_weak
            and volume_ratio >= 1.5
            and volume_previous >= 0.7
            and close_position >= 0.65
            and upper_wick <= 0.35
        )
        if not resistance_rescued and not leader_resistance_override and not structure_resistance_pending:
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
    alert["screening_metrics"] = {
        **alert["screening_metrics"],
        "retest_confirmed": retest_confirmed,
        "trade_flow_leader": flow_leader, "trade_flow_context": flow_context,
        "flow_entry_context": flow_entry_context,
        "flow_volume_mode": rsi_context.get("volume_mode", "standard") if flow_leader else None,
        "flow_execution_override": flow_execution_override,
        "flow_continuation_entry": flow_continuation_entry,
        "continuation_context": continuation_context,
        "completed_breakout_entry": completed_breakout_entry,
        "completed_structure_entry": completed_structure_entry,
        "completed_structure_context": structure_context,
        "completed_structure_checks": structure_checks,
        "completed_structure_blockers": [k for k, v in structure_checks.items() if v is False],
        "completed_structure_unverified": [k for k, v in structure_checks.items() if v is None],
        "completed_structure_proof_blockers": structure_context.get("blockers", []),
        "recent_completed_minute_starts": [c.get("candle_date_time_utc") for c in c1[:12]],
        "structure_book_context": structure_book,
        "ordinary_breakout_context": rsi_context,
        "wb_confirmation_timeframe": wb_confirmation_timeframe,
        "wb_five_breakout": wb_five_breakout,
        "wb_five_retest": wb_five_retest,
        "wb_one_context": double_bb_one,
        "wb_five_context": double_bb_five,
        "completed_wb_retest_entry": completed_wb_retest_entry,
        "recent_ten_completed_minutes_contiguous": recent_minute_candles_contiguous(candles_1m, context_now),
        "hourly_context": higher,
        "rsi_1m": rsi1, "rsi_5m": rsi5, "wb_confirmed": wb_confirmed,
        "volume_ratio_5m": _volume_metrics(c5)[0],
        "volume_ratio_15m": _volume_metrics(c15)[0],
        "orderbook_ratio": book_ratio, "spread_pct": spread,
        "tick_spread_exception": tick_spread_exception,
        "execution_spread_context": execution_spread_context,
        "trade_value_24h_krw": trade_value_24h,
        "btc_crash": btc_crash, "resistance_room_pct": room,
        "resistance_levels": resistance_levels,
        "resistance_admission_basis": "actual_target_risk_and_cost" if structure_resistance_pending else "distance_floor",
        "rsi_breakout_exception": rsi_breakout_exception,
        "breadth_breakout_exception": breadth_breakout_exception,
        "cleared_resistance_levels": cleared_resistance_levels,
        "explosive_context": explosive_context,
    }
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
    if hourly_trend_core or explosive_core or completed_wb_retest_entry or narrow_market_fast_core:
        # Plan a 5m initial invalidation before entry, never widen it later.
        support = min(min(float(c["low_price"]) for c in c5[:3]), breakout)
        if explosive_core or flow_leader or completed_wb_retest_entry:
            support = min(float((rsi_context if flow_leader or completed_wb_retest_entry else explosive_context)["low"]), breakout)
        if flow_continuation_entry:
            support = max(breakout, float(continuation_context["low"]))
        if completed_structure_entry:
            support = min(float(structure_context["low"]), breakout)
        atr_risk = max(_atr(c5) * 0.8, tick * 2)
    stop_raw = min(support * 0.998, entry_reference - atr_risk)
    alert["risk_plan_diagnostics"] = {
        "stage": "structural_stop", "entry_reference_price": entry_reference,
        "support": support, "atr_risk": atr_risk, "tick_size": tick,
        "stop_raw": stop_raw,
        "raw_stop_loss_pct": (1 - stop_raw / entry_reference) * 100,
        "max_stop_loss_pct": config.max_stop_loss_pct,
    }
    if (
        (hourly_trend_core or explosive_core or completed_wb_retest_entry or narrow_market_fast_core)
        and (1 - stop_raw / entry_reference) * 100 > config.max_stop_loss_pct
    ):
        return None, ["5분 구조 손절폭이 허용 손실폭 초과"]
    stop = _round_tick(
        max(
            stop_raw,
            entry_reference * (1 - config.max_stop_loss_pct / 100),
        ),
        tick,
        "down",
    )
    alert["risk_plan_diagnostics"].update(
        stage="rounded_stop", stop_price=stop,
        rounded_stop_loss_pct=(1 - stop / entry_reference) * 100,
    )
    if (
        (hourly_trend_core or explosive_core or completed_wb_retest_entry or narrow_market_fast_core)
        and (1 - stop / entry_reference) * 100 > config.max_stop_loss_pct + 1e-9
    ):
        return None, ["호가 단위 반영 후 5분 구조 손절폭 초과"]
    risk = max(entry_reference - stop, tick * 2)
    structural_risk_reward = (target_resistance - entry_reference) / risk

    setup_score = {
        "consolidation_rebreakout": 25,
        "breakout": 22,
        "price_volume_surge": 20,
        "leader_volume_acceleration": 20,
        "persistent_leader_acceleration": 24,
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
    if hourly_trend_core:
        trend_score = (
            8
            + (6 if higher["fifteen"]["above_ma20"] else 0)
            + (4 if current >= float(five["ma20"]) else 0)
            + (2 if higher["four_hour"].get("above_ma20") else 0)
        )
    btc_strong = (
        float(btc_five["latest_price"]) >= float(btc_five["ma20"])
        and float(btc_fifteen["latest_price"]) >= float(btc_fifteen["ma20"])
        and _ma20_slope(b5) >= 0
    )
    liquid_market = trade_value_24h >= config.min_trade_value_24h_krw * 5
    market_score = 15 if btc_strong else 12
    risk_notes: list[str] = list(soft_warnings)
    if breadth_softened:
        risk_notes.append(
            "시장 약세 중 상대강도 선도 유지: 시장 확산도는 감점 반영"
        )
    if flow_leader:
        risk_notes.append("실제 매수 체결 지속으로 호가 비율·확산도 차단 대체: 깊이·완료봉·60초 생존 검증, 감점 유지")
    if flow_continuation_entry:
        risk_notes.append("완료 15분 추세·5분 저점 상승·60초 매수 지속으로 회복 초반 검증: RSI 감점 유지, 95 이상 제외")
    if breadth_breakout_exception:
        risk_notes.append("완료 5·15분 돌파·상위 2% 선도주 확인: 확산도 차단만 감점 전환, 60초 재검증")
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
        if config.double_bb_enabled and not wb_confirmed:
            risk_notes.append("WB 미확정 초고속 신호: 첫 눌림 전 추격 금지")
    if explosive_core:
        risk_notes.append("완료 5·15분 폭발적 돌파 검증: 1분 과열·WB는 감점, 구조 손절·추격·BTC 급락은 차단 유지")
    if rsi_breakout_exception:
        risk_notes.append("RSI 단독 차단 예외: 완료 5·15분 거래량·WB·정상 호가 확인, 과열 감점 유지")
    if structure_resistance_pending:
        risk_notes.append("근접 저항 가격 유지·실제 1차 목표 손익비와 비용 차감 손익비 검증")
    if cleared_resistance_levels:
        risk_notes.append("완료 5분 종가로 돌파한 과거 저항의 지지 전환 확인")
    if elite_leader_retest:
        risk_notes.append(
            "최상위 선도주 첫 재지지 예외: 5분 RSI/WB 미확정은 감점 유지, "
            "생존·저항·손익비 검증 필수"
        )
    if not resistance_confirmed:
        risk_notes.append("반복 확인된 상단 구조 저항 없음")
    score_penalty = config.day_overheat_score_penalty if day_overheated else 0
    if btc_weak:
        score_penalty += min(market_score, config.btc_weak_score_penalty)
    elif btc_caution:
        score_penalty += min(market_score, config.btc_weak_score_penalty // 2)
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
    wb_bonus = config.double_bb_score_bonus if wb_confirmed else 0
    # Award the independently completed price proof in place of (never in
    # addition to) WB evidence. Do not penalize the very WB absence that this
    # separately verified lane is designed to handle.
    price_structure_bonus = config.double_bb_score_bonus if completed_structure_entry and not wb_confirmed else 0
    if price_structure_bonus:
        risk_notes.append("WB 보조 미확인·완료 가격 구조를 별도 검증(필수 실행·위험 조건 유지)")
    if config.double_bb_enabled and wb_ready and not wb_confirmed and not completed_structure_entry:
        score_penalty += config.double_bb_unconfirmed_penalty
        risk_notes.append(
            "WB 동시 돌파 미확정 "
            f"-{config.double_bb_unconfirmed_penalty}점"
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
    # Bonuses may fill an area, but must not erase explicit risk deductions
    # through saturation above 100. A score of 100 has no deducted warnings.
    score_before_penalties = min(
        100,
        setup_score
        + volume_score
        + trend_score
        + market_score
        + (12 if room >= 7 and structural_risk_reward >= 2.5 else 10)
        + (12 if book_ratio >= 1.2 and spread <= 0.2 and liquid_market else 10)
        + impulse_bonus
        + relative_strength_bonus
        + early_leader_bonus
        + wb_bonus
        + price_structure_bonus
        + regime_bonus,
    )
    score = max(0, score_before_penalties - score_penalty)
    risk_off_low_room = bool(
        market_regime == "risk_off"
        and room < config.min_resistance_room_pct
    )
    if risk_off_low_room:
        score = min(score, config.risk_off_low_room_score_cap)
        risk_notes.append(
            "약세장·저항 여유 부족 점수 상한 "
            f"{config.risk_off_low_room_score_cap}점"
        )
    effective_min_score = config.min_score
    if config.availability_balance_enabled:
        effective_min_score = min(
            config.min_score, config.availability_min_score
        )
    if explosive_core:
        effective_min_score = config.explosive_leader_min_score
    if flow_leader:
        effective_min_score = config.trade_flow_leader_min_score
    if completed_structure_entry:
        effective_min_score = max(86, config.trade_flow_leader_min_score)
    if score < effective_min_score:
        return None, [f"후보 점수 부족({score}/{effective_min_score})"]

    availability_tier = score < config.min_score and not explosive_core and not flow_leader and not completed_structure_entry
    if availability_tier:
        # The lower score tier exists only to restore a small number of usable
        # messages. It must still pass every direct entry-safety boundary and
        # cannot borrow points from an overheated or weak market environment.
        availability_safe = bool(
            not elevated_risk
            and not btc_weak
            and not risk_off_low_room
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
        and structural_risk_reward >= 2.5
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
    hourly_trend_extension = bool(
        hourly_trend_core and score >= 90 and not btc_weak and not day_overheated
        and (not flow_continuation_entry or higher.get("hourly_established"))
    )
    if sustained_retest and not hourly_trend_extension:
        return None, ["지속 재지지 추세보유 자격 미확인"]
    if hourly_trend_extension or relative_trend_extension:
        target1 = _round_tick(
            min(target_resistance, entry_reference * 1.03), tick, "down"
        )
        target2 = _round_tick(
            entry_reference * (1 + config.trend_target_2_pct / 100), tick, "up"
        )
        target_mode = (
            "1시간 추세보유형" if hourly_trend_extension else "상대강도 추세추적형"
        )
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
    # Only an established, freshly confirmed hourly retest may use partial
    # exits. Keep normal/fast entries on the existing first-target rule.
    exit_plan = None
    if (
        (hourly_trend_extension or relative_trend_extension)
        and higher.get("ready") and higher.get("hourly_established")
        and higher.get("fifteen_intact") and higher["fifteen"]["above_ma20"]
        and wb_confirmed and retest_confirmed and score >= 90
        and book_persistent and execution_spread_ok
        and _volume_metrics(fresh_five)[0] >= 1.5
        and _volume_metrics(fresh_fifteen)[0] >= 1.0
        and recent_minute_candles_contiguous(candles_1m, context_now)
    ):
        # Do not enlarge a close confirmed resistance using a leader override.
        # Without historical resistance, use the existing conservative room
        # projection, never the nominal 10% expansion target.
        runner_ceiling = _round_tick(
            min(resistance, entry_reference * (1 + config.trend_target_2_pct / 100)),
            tick, "down",
        )
        if runner_ceiling > target1:
            target2 = runner_ceiling
            exit_plan = {
                "mode": "partial_50_50", "target_1_fraction": 0.5,
                "runner_fraction": 0.5, "runner_ceiling": runner_ceiling,
                "ceiling_basis": "confirmed_resistance" if resistance_confirmed
                else "conservative_projection",
                "protect_after_target_1": "entry_price",
            }
    if target1 <= entry_reference:
        return None, ["호가 단위 반영 후 실제 1차 목표 여유 없음"]
    metrics = exit_plan_metrics(entry_reference, stop, target1, target2, exit_plan)
    risk_reward = float(metrics["risk_reward"])
    execution_cost = {}
    if tick_spread_exception or flow_execution_override or completed_structure_entry:
        execution_cost = execution_cost_metrics(entry_reference, stop, target1, target2,
            exit_plan, spread, config.tick_spread_cost_buffer_pct)
        alert["screening_metrics"]["execution_cost"] = execution_cost
        if execution_cost["net_risk_reward"] < config.tick_spread_min_net_risk_reward:
            return None, [f"최소 호가 단위 예외의 비용 차감 손익비 부족({execution_cost['net_risk_reward']:.2f} < {config.tick_spread_min_net_risk_reward:.2f})"]
    alert["risk_plan_diagnostics"] = {
        **alert["risk_plan_diagnostics"], "stage": "target_plan",
        "entry_reference_price": entry_reference, "stop_price": stop,
        "target_1": target1, "target_2": target2, "exit_plan": exit_plan,
        **metrics, "min_risk_reward": config.min_risk_reward,
    }
    if exit_plan and float(metrics["first_target_risk_reward"]) < 1.0:
        return None, ["부분 청산 1차 목표 손익비 부족(1.00 미만)"]
    if flow_leader and float(metrics["first_target_risk_reward"]) < 2.0:
        return None, ["체결 지속형 1차 목표 손익비 부족(2.00 미만)"]
    required_risk_reward = 1.4 if narrow_market_fast_core else config.min_risk_reward
    if risk_reward < required_risk_reward:
        return None, [
            ("부분 청산 계획 손익비 부족" if exit_plan else "실제 1차 목표 기준 손익비 부족")
            + f"({risk_reward:.2f} < {required_risk_reward:.2f})"
        ]
    if hourly_trend_extension or relative_trend_extension or strong_extension:
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
    if explosive_core:
        reasons[0:2] = ["완료 5분 매물대 돌파·첫 재지지" if explosive_context.get("retest")
                        else "완료 5분 매물대 거래량 동반 돌파",
                        f"완료 거래량 5분 {explosive_context['volume_5m']:.2f}배 · 15분 {explosive_context['volume_15m']:.2f}배"]
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
        reasons.insert(
            0,
            (
                "09시 전후 거래대금 선행 가속에서 조기 포착"
                if alert.get("notify_early_watch", True)
                else "장중 상대강도 선도주 거래대금 급가속"
            ),
        )
    if alert.get("signal") == "persistent_leader_acceleration":
        reasons.insert(0, "5·15·60분 상대강도와 거래대금이 유지된 지속형 선도주")
    if relative_ready and relative_eligible:
        reasons.insert(
            0,
            "상대강도 "
            f"{int(alert.get('relative_strength_rank') or 0)}/"
            f"{int(alert.get('relative_strength_universe') or 0)}위",
        )
    if retest_confirmed and relative_ready:
        reasons.insert(0, "첫 눌림·돌파선 재지지 확인")
    if completed_wb_retest_entry:
        reasons.insert(0, "완료 5분 WB 첫 눌림 구조·거래량 확인(1분 실행 품질 유지)")
    if flow_leader:
        reasons.insert(0, "60초 실제 매수 체결 지속·15분 추세·완료 5분 저점 상승 확인"
                       if flow_continuation_entry else "60초 실제 매수 체결 지속·15분/1시간 추세·완료 5분 구조 확인")
    if completed_breakout_entry:
        reasons.insert(0, "완료 5분봉 거래량 동반 돌파 확인(첫 눌림 대안)")
    if completed_structure_entry:
        reasons = [r for r in reasons if r != "완료 1분봉 돌파 확정"]
        reasons.insert(0, str(structure_context["status"]) + "·가까운 반복 호가 확인")
        reasons.insert(0, "새 돌파 구조에서 진입·손절 계획 재계산(첫 눌림과 구분)")
    if wb_confirmed:
        reasons.insert(0, str(double_bb["status"]))
    if early_trend and relative_ready:
        reasons.insert(0, "상승 초기 가속 구간")
    if early_leader_lane:
        reasons.insert(0, "상대강도 선도주 정밀 통과")
    if persistent_leader_core:
        reasons.insert(0, "지속형 선도주 재가속 경로 통과")
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
    elif btc_caution:
        risk_notes.append(
            f"BTC 단기 이평 약세 주의 -{config.btc_weak_score_penalty // 2}점(완료봉 급락 아님)"
        )
    if higher.get("ready"):
        reasons.insert(
            0,
            f"1시간 {higher['hourly']['status']} · 4시간 {higher['four_hour']['status']}",
        )
    if day_overheated:
        risk_notes.append(
            f"당일 과열 감점 -{config.day_overheat_score_penalty}점({day_change:.1f}%)"
        )
    suggested_position_pct = 5
    if hourly_trend_extension:
        stop_loss_pct = (1 - stop / entry_reference) * 100
        # At most 0.1% of investment capital at the planned initial stop.
        suggested_position_pct = _round_tick(
            min(5.0, 10.0 / max(stop_loss_pct, 0.01)), 0.01, "down"
        )

    return {
        "signal_id": alert.get("signal_id"),
        "parent_signal_id": alert.get("parent_signal_id"),
        "origin_signal_price": origin_signal_price or None,
        "fresh_breakout_extension_pct": round(fresh_breakout_extension_pct, 2),
        "time_utc": alert.get("time_utc"),
        "market": alert["market"],
        "signal": "entry_candidate",
        "label": "조건부 진입 후보",
        "source_signal": alert["signal"],
        "is_reentry": bool(alert.get("is_reentry")),
        "watchlist_recheck": bool(alert.get("watchlist_recheck")),
        "leader_pullback_recheck": bool(alert.get("leader_pullback_recheck")),
        "fresh_breakout_recheck": fresh_breakout_recheck,
        "selection_lane": (
            "completed_structure" if completed_structure_entry
            else "trade_flow_leader" if flow_leader
            else "explosive_leader" if explosive_core
            else "fast_leader" if fast_leader_core
            else "early_leader" if early_leader_lane else "standard"
        ),
        "explosive_context": explosive_context if explosive_core else None,
        "rsi_breakout_exception": rsi_breakout_exception,
        "completed_breakout_entry": completed_breakout_entry,
        "completed_structure_entry": completed_structure_entry,
        "completed_structure_context": structure_context if completed_structure_entry else None,
        "structure_book_context": structure_book if completed_structure_entry else None,
        "completed_wb_retest_entry": completed_wb_retest_entry,
        "trade_flow_leader": flow_leader, "trade_flow_context": flow_context if flow_leader else None,
        "flow_volume_mode": rsi_context.get("volume_mode", "standard") if flow_leader else None,
        "flow_entry_context": rsi_context if flow_leader else None,
        "flow_execution_override": flow_execution_override,
        "flow_continuation_entry": flow_continuation_entry,
        "continuation_context": continuation_context if flow_continuation_entry else None,
        "breadth_breakout_exception": breadth_breakout_exception,
        "cleared_resistance_levels": cleared_resistance_levels,
        "volume_timeframe": "5분" if explosive_core or flow_leader or completed_structure_entry else "1분",
        "fast_leader": fast_leader_core and not explosive_core,
        "narrow_market_fast_leader": narrow_market_fast_core,
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
        "holding_mode": (
            "hourly_structure" if hourly_trend_extension else "initial_signal"
        ),
        "higher_timeframe_context": higher,
        "sustained_retest": sustained_retest,
        "score_before_penalties": score_before_penalties,
        "score_penalty": score_penalty,
        "price_structure_score_bonus": price_structure_bonus,
        "completed_5m_volume_ratio": round(_volume_metrics(c5)[0], 2),
        "completed_15m_volume_ratio": round(_volume_metrics(c15)[0], 2),
        "stop_timeframe": "5분 구조" if hourly_trend_core or explosive_core or completed_wb_retest_entry else "초기 신호 구조",
        "price_tick": tick,
        "tick_spread_exception": tick_spread_exception,
        "execution_spread_context": execution_spread_context if tick_spread_exception else None,
        "execution_cost": execution_cost if tick_spread_exception or flow_execution_override or completed_structure_entry else None,
        "execution_cost_buffer_pct": config.tick_spread_cost_buffer_pct,
        "minimum_net_risk_reward": config.tick_spread_min_net_risk_reward,
        "trend_management": (
            "1차 50% 익절·진입가 보호, 2차 잔여 50% 청산·확장선은 별도 관찰"
            if exit_plan else
            "1차 일부 익절·진입가 보호, 잔여분 15분 확정 저점·1시간 추세 관리"
            if hourly_trend_extension
            else (
                "1차 목표 후 진입가 보호, 10%·15%·20% 추세 관찰"
                if relative_trend_extension or strong_extension
                else "고정 목표 관리"
            )
        ),
        "resistance_price": resistance,
        "risk_reward_reference_price": target1,
        "exit_plan": exit_plan,
        "risk_reward_basis": metrics["risk_reward_basis"],
        "first_target_risk_reward": round(float(metrics["first_target_risk_reward"]), 4),
        "resistance_confirmed": resistance_confirmed,
        "resistance_room_pct": round(room, 2),
        "leader_resistance_override": leader_resistance_override,
        "breakout_cluster_ignored": breakout_cluster_ignored,
        "risk_reward": round(risk_reward, 2),
        "structural_risk_reward": round(structural_risk_reward, 2),
        "breakout_level": breakout,
        "valid_seconds": (
            min(config.valid_seconds, 120) if fast_leader_core or explosive_core else config.valid_seconds
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
        "momentum_5m_pct": (round(momentum_5m, 2) if relative_ready else None),
        "momentum_15m_pct": (
            round(momentum_15m, 2) if momentum_15m is not None else None
        ),
        "momentum_60m_pct": alert.get("momentum_60m_pct"),
        "first_retest_confirmed": retest_confirmed or completed_wb_retest_entry,
        "double_bb_enabled": config.double_bb_enabled,
        "double_bb_timeframe": wb_confirmation_timeframe,
        "double_bb_ready": bool(double_bb.get("ready")),
        "double_bb_status": double_bb.get("status"),
        "double_bb_confirmed": wb_confirmed,
        "double_bb_true_breakout": bool(double_bb.get("true_breakout")),
        "double_bb_first_retest": bool(double_bb.get("first_retest")),
        "double_bb_fake_breakout": wb_fake_breakout,
        "double_bb_fast_upper": double_bb.get("fast_upper"),
        "double_bb_standard_upper": double_bb.get("standard_upper"),
        "double_bb_structure_high": double_bb.get("structure_high"),
        "rsi_1m": round(rsi1, 1),
        "rsi_5m": round(rsi5, 1),
        "orderbook_bid_ask_ratio": round(book_ratio, 2),
        "spread_pct": round(spread, 3),
        "completed_1m_volume_ratio": round(one_volume_ratio, 2),
        "volume_vs_previous": round(volume_previous, 2),
        "close_position": round(close_position, 2),
        "upper_wick_ratio": round(upper_wick, 2),
        "btc_day_change_pct": round(btc_change, 2),
        "btc_weak": btc_weak,
        "btc_caution": btc_caution,
        "btc_crash": btc_crash,
        "day_overheated": day_overheated,
        "elevated_risk": elevated_risk,
        "elevated_candle_balance": elevated_candle_balance,
        "availability_balanced": bool(soft_warnings),
        "availability_tier": availability_tier,
        "risk_notes": risk_notes,
        "reasons": reasons[:5],
        "trade_value_24h_krw": round(trade_value_24h),
    }, []
