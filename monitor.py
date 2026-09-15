"""Always-on Upbit public trade monitor with optional Telegram alerts."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import threading
import time
import uuid
from collections import Counter, defaultdict, deque
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from statistics import median
from typing import Any, Callable

import httpx
import websockets

from candidate_analysis import CandidateConfig, evaluate_candidate
from upbit_client import UpbitPublicClient

LOGGER = logging.getLogger("upbit-monitor")
# Telegram places the bot token in the request URL. Disable transport-level
# logging so the token can never be copied into Railway's persistent logs.
logging.getLogger("httpx").disabled = True
logging.getLogger("httpcore").disabled = True
_WS_URL = "wss://api.upbit.com/websocket/v1"
_KST = timezone(timedelta(hours=9))


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


def _env_minute_of_day(name: str, default: str) -> int:
    raw = os.getenv(name, default).strip()
    try:
        hour_text, minute_text = raw.split(":", 1)
        hour = int(hour_text)
        minute = int(minute_text)
    except (AttributeError, TypeError, ValueError):
        hour_text, minute_text = default.split(":", 1)
        hour = int(hour_text)
        minute = int(minute_text)
    return max(0, min(1439, hour * 60 + minute))


@dataclass(frozen=True)
class MonitorConfig:
    markets: tuple[str, ...]
    all_krw_markets: bool
    price_surge_1m_pct: float
    breakout_pct: float
    volume_ratio: float
    min_trade_value_krw: float
    alert_cooldown_seconds: int
    max_alerts_per_minute: int
    rebreakout_enabled: bool
    rebreakout_consolidation_seconds: int
    rebreakout_max_range_pct: float
    rebreakout_price_buffer_pct: float
    rebreakout_min_volume_ratio: float
    relative_strength_enabled: bool
    relative_strength_min_universe: int
    relative_strength_top_percent: float
    relative_strength_min_5m_pct: float
    relative_strength_stale_seconds: int
    preleader_enabled: bool
    preleader_start_minute_kst: int
    preleader_end_minute_kst: int
    preleader_check_interval_seconds: int
    preleader_min_value_ratio_10m: float
    preleader_min_value_ratio_30m: float
    preleader_min_3m_pct: float
    preleader_min_5m_pct: float
    preleader_max_percentile: float
    preleader_rank_improvement_pct: float
    preleader_cooldown_seconds: int

    @classmethod
    def from_env(cls) -> "MonitorConfig":
        raw_markets = os.getenv("MONITOR_MARKETS", "ALL_KRW")
        values = tuple(
            value.strip().upper() for value in raw_markets.split(",") if value.strip()
        )
        all_markets = not values or "ALL_KRW" in values
        selected = tuple(value for value in values if value != "ALL_KRW")
        return cls(
            markets=selected,
            all_krw_markets=all_markets,
            price_surge_1m_pct=_env_float("MONITOR_PRICE_SURGE_1M_PCT", 1.5),
            breakout_pct=_env_float("MONITOR_BREAKOUT_PCT", 0.4),
            volume_ratio=_env_float("MONITOR_VOLUME_RATIO", 2.0),
            min_trade_value_krw=_env_float("MONITOR_MIN_TRADE_VALUE_KRW", 50_000_000),
            alert_cooldown_seconds=max(
                60, _env_int("MONITOR_ALERT_COOLDOWN_SECONDS", 600)
            ),
            max_alerts_per_minute=max(1, _env_int("MONITOR_MAX_ALERTS_PER_MINUTE", 5)),
            rebreakout_enabled=_enabled("MONITOR_REBREAKOUT_ENABLED", True),
            rebreakout_consolidation_seconds=max(
                900,
                min(
                    7200,
                    _env_int("MONITOR_REBREAKOUT_CONSOLIDATION_SECONDS", 1800),
                ),
            ),
            rebreakout_max_range_pct=max(
                0.5, _env_float("MONITOR_REBREAKOUT_MAX_RANGE_PCT", 3.0)
            ),
            rebreakout_price_buffer_pct=max(
                0.1, _env_float("MONITOR_REBREAKOUT_PRICE_BUFFER_PCT", 0.3)
            ),
            rebreakout_min_volume_ratio=max(
                1.5, _env_float("MONITOR_REBREAKOUT_MIN_VOLUME_RATIO", 3.0)
            ),
            relative_strength_enabled=_enabled(
                "MONITOR_RELATIVE_STRENGTH_ENABLED", True
            ),
            relative_strength_min_universe=max(
                20, _env_int("MONITOR_RELATIVE_STRENGTH_MIN_UNIVERSE", 80)
            ),
            relative_strength_top_percent=max(
                5.0,
                min(
                    40.0,
                    _env_float("MONITOR_RELATIVE_STRENGTH_TOP_PERCENT", 10.0),
                ),
            ),
            relative_strength_min_5m_pct=_env_float(
                "MONITOR_RELATIVE_STRENGTH_MIN_5M_PCT", 1.0
            ),
            relative_strength_stale_seconds=max(
                30, _env_int("MONITOR_RELATIVE_STRENGTH_STALE_SECONDS", 120)
            ),
            preleader_enabled=_enabled("MONITOR_PRELEADER_ENABLED", True),
            preleader_start_minute_kst=_env_minute_of_day(
                "MONITOR_PRELEADER_START_KST", "08:45"
            ),
            preleader_end_minute_kst=_env_minute_of_day(
                "MONITOR_PRELEADER_END_KST", "09:15"
            ),
            preleader_check_interval_seconds=max(
                5, _env_int("MONITOR_PRELEADER_CHECK_INTERVAL_SECONDS", 15)
            ),
            preleader_min_value_ratio_10m=max(
                1.2, _env_float("MONITOR_PRELEADER_MIN_VALUE_RATIO_10M", 2.0)
            ),
            preleader_min_value_ratio_30m=max(
                1.1, _env_float("MONITOR_PRELEADER_MIN_VALUE_RATIO_30M", 1.5)
            ),
            preleader_min_3m_pct=max(
                0.1, _env_float("MONITOR_PRELEADER_MIN_3M_PCT", 0.4)
            ),
            preleader_min_5m_pct=max(
                0.2, _env_float("MONITOR_PRELEADER_MIN_5M_PCT", 0.6)
            ),
            preleader_max_percentile=max(
                5.0,
                min(40.0, _env_float("MONITOR_PRELEADER_MAX_PERCENTILE", 20.0)),
            ),
            preleader_rank_improvement_pct=max(
                0.0,
                _env_float("MONITOR_PRELEADER_RANK_IMPROVEMENT_PCT", 5.0),
            ),
            preleader_cooldown_seconds=max(
                300, _env_int("MONITOR_PRELEADER_COOLDOWN_SECONDS", 1800)
            ),
        )


@dataclass
class _SecondBucket:
    second: int
    opening_price: float
    high_price: float
    low_price: float
    closing_price: float
    trade_value: float


class MonitorState:
    """Thread-safe operational state shared with MCP inspection tools."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self.started_at: str | None = None
        self.connected = False
        self.connected_at: str | None = None
        self.last_message_at: str | None = None
        self.last_error: str | None = None
        self.reconnect_count = 0
        self.message_count = 0
        self.market_count = 0
        self.notification_mode = "log_only"
        self.recent_alerts: deque[dict[str, Any]] = deque(maxlen=100)
        self.candidate_outcomes: deque[dict[str, Any]] = deque(maxlen=500)
        self.signal_outcomes: deque[dict[str, Any]] = deque(maxlen=1000)
        self.trend_outcomes: deque[dict[str, Any]] = deque(maxlen=500)
        self.screening_records: deque[dict[str, Any]] = deque(maxlen=2000)
        self.candidate_delivery_day_kst: str | None = None
        self.candidate_delivery_count_today = 0
        self.candidate_delivery_min_daily = 0
        self.candidate_delivery_max_daily = 0
        self.last_candidate_delivery_at: str | None = None

    @staticmethod
    def _now() -> str:
        return datetime.now(timezone.utc).isoformat()

    def mark_started(self, market_count: int, notification_mode: str) -> None:
        with self._lock:
            self.started_at = self.started_at or self._now()
            self.market_count = market_count
            self.notification_mode = notification_mode

    def mark_connected(self) -> None:
        with self._lock:
            self.connected = True
            self.connected_at = self._now()
            self.last_error = None

    def mark_message(self) -> None:
        with self._lock:
            self.message_count += 1
            self.last_message_at = self._now()

    def mark_disconnected(self, error: Exception) -> None:
        with self._lock:
            self.connected = False
            self.reconnect_count += 1
            self.last_error = f"{type(error).__name__}: {error}"[:500]

    def add_alert(self, alert: dict[str, Any]) -> None:
        with self._lock:
            self.recent_alerts.appendleft(dict(alert))

    def add_candidate_outcome(self, outcome: dict[str, Any]) -> None:
        with self._lock:
            self.candidate_outcomes.appendleft(dict(outcome))
        LOGGER.warning("CANDIDATE_OUTCOME %s", json.dumps(outcome, ensure_ascii=False))

    def add_signal_outcome(self, outcome: dict[str, Any]) -> None:
        """Record every screened raw signal, including candidates not sent."""
        with self._lock:
            self.signal_outcomes.appendleft(dict(outcome))
        LOGGER.warning("RAW_SIGNAL_OUTCOME %s", json.dumps(outcome, ensure_ascii=False))

    def add_trend_outcome(self, outcome: dict[str, Any]) -> None:
        with self._lock:
            self.trend_outcomes.appendleft(dict(outcome))
        LOGGER.warning("TREND_OUTCOME %s", json.dumps(outcome, ensure_ascii=False))

    def add_screening_record(self, record: dict[str, Any]) -> None:
        with self._lock:
            self.screening_records.appendleft(dict(record))
        LOGGER.info("CANDIDATE_SCREENING %s", json.dumps(record, ensure_ascii=False))

    def update_candidate_delivery(
        self,
        *,
        day_kst: str,
        count: int,
        minimum: int,
        maximum: int,
        delivered: bool = False,
    ) -> None:
        with self._lock:
            self.candidate_delivery_day_kst = day_kst
            self.candidate_delivery_count_today = count
            self.candidate_delivery_min_daily = minimum
            self.candidate_delivery_max_daily = maximum
            if delivered:
                self.last_candidate_delivery_at = self._now()

    @staticmethod
    def _performance_summary(outcomes: list[dict[str, Any]]) -> dict[str, Any]:
        targets = sum(item.get("result") == "target_first" for item in outcomes)
        stops = sum(item.get("result") == "stop_first" for item in outcomes)
        decided = targets + stops
        return {
            "sample_count": len(outcomes),
            "decided_count": decided,
            "target_first": targets,
            "stop_first": stops,
            "expired": sum(item.get("result") == "expired" for item in outcomes),
            "target_first_rate_pct": (
                round(targets / decided * 100, 2) if decided else None
            ),
            "recent": outcomes[:100],
        }

    def candidate_performance(self) -> dict[str, Any]:
        with self._lock:
            outcomes = list(self.candidate_outcomes)
            signal_outcomes = list(self.signal_outcomes)
            trend_outcomes = list(self.trend_outcomes)
            screening_records = list(self.screening_records)
        targets = sum(item.get("result") == "target_1_first" for item in outcomes)
        stops = sum(item.get("result") == "stop_first" for item in outcomes)
        decided = targets + stops
        return {
            "sample_count": len(outcomes),
            "decided_count": decided,
            "target_1_first": targets,
            "stop_first": stops,
            "expired": sum(item.get("result") == "expired" for item in outcomes),
            "target_1_first_rate_pct": (
                round(targets / decided * 100, 2) if decided else None
            ),
            "recent": outcomes[:100],
            "raw_signal_performance": self._performance_summary(signal_outcomes),
            "six_hour_trend_performance": {
                "sample_count": len(trend_outcomes),
                "target_1_reached": sum(
                    bool(item.get("target_1_reached")) for item in trend_outcomes
                ),
                "target_2_reached": sum(
                    bool(item.get("target_2_reached"))
                    or item.get("result") == "target_2_reached"
                    for item in trend_outcomes
                ),
                "target_3_reached": sum(
                    bool(item.get("target_3_reached"))
                    for item in trend_outcomes
                ),
                "target_4_reached": sum(
                    bool(item.get("target_4_reached"))
                    or item.get("result") == "target_4_reached"
                    for item in trend_outcomes
                ),
                "stop_before_target_1": sum(
                    item.get("result") == "stop_before_target_1"
                    for item in trend_outcomes
                ),
                "protected_after_target_1": sum(
                    item.get("result") == "protected_after_target_1"
                    for item in trend_outcomes
                ),
                "expired": sum(
                    item.get("result") == "expired" for item in trend_outcomes
                ),
                "recent": trend_outcomes[:100],
            },
            "screening": {
                "sample_count": len(screening_records),
                "accepted": sum(
                    item.get("decision") == "accepted" for item in screening_records
                ),
                "rejected": sum(
                    item.get("decision") != "accepted" for item in screening_records
                ),
                "recent": screening_records[:100],
            },
        }

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            return {
                "enabled": _enabled("ENABLE_MARKET_MONITOR"),
                "started_at_utc": self.started_at,
                "connected": self.connected,
                "connected_at_utc": self.connected_at,
                "last_message_at_utc": self.last_message_at,
                "last_error": self.last_error,
                "reconnect_count": self.reconnect_count,
                "message_count": self.message_count,
                "market_count": self.market_count,
                "notification_mode": self.notification_mode,
                "recent_alert_count": len(self.recent_alerts),
                "candidate_outcome_count": len(self.candidate_outcomes),
                "raw_signal_outcome_count": len(self.signal_outcomes),
                "six_hour_trend_outcome_count": len(self.trend_outcomes),
                "candidate_screening_count": len(self.screening_records),
                "candidate_delivery_day_kst": self.candidate_delivery_day_kst,
                "candidate_delivery_count_today": self.candidate_delivery_count_today,
                "candidate_delivery_target_daily": {
                    "minimum": self.candidate_delivery_min_daily,
                    "maximum": self.candidate_delivery_max_daily,
                },
                "last_candidate_delivery_at_utc": self.last_candidate_delivery_at,
            }

    def alerts(self, limit: int, market: str | None = None) -> list[dict[str, Any]]:
        with self._lock:
            items = list(self.recent_alerts)
        if market:
            normalized = market.strip().upper()
            items = [item for item in items if item.get("market") == normalized]
        return items[: max(1, min(limit, 100))]

    def has_recent_signal(self, market: str, signal: str, seconds: int) -> bool:
        cutoff = time.time() - seconds
        with self._lock:
            items = list(self.recent_alerts)
        for item in items:
            if item.get("market") != market or item.get("signal") != signal:
                continue
            try:
                observed = datetime.fromisoformat(str(item["time_utc"])).timestamp()
            except (KeyError, TypeError, ValueError):
                continue
            if observed >= cutoff:
                return True
        return False


MONITOR_STATE = MonitorState()


class SignalEngine:
    """Build rolling one-second buckets and emit deterministic market signals."""

    def __init__(self, config: MonitorConfig) -> None:
        self.config = config
        self._windows: dict[str, deque[_SecondBucket]] = defaultdict(deque)
        self._last_evaluated_second: dict[str, int] = {}
        self._last_alert_at: dict[tuple[str, str], int] = {}
        self._global_alerts: deque[int] = deque()
        self._momentum_prices: dict[str, deque[tuple[int, float]]] = defaultdict(
            deque
        )
        self._last_preleader_checked_at: dict[str, int] = {}
        self._last_preleader_at: dict[str, int] = {}
        self._relative_rank_history: dict[
            str, deque[tuple[int, float]]
        ] = defaultdict(deque)

    @staticmethod
    def _rolling_return(
        samples: deque[tuple[int, float]], now: int, seconds: int
    ) -> float | None:
        """Return a rolling change from five-second price samples."""
        if not samples or now - samples[0][0] < seconds * 0.9:
            return None
        cutoff = now - seconds
        reference = None
        for sample_second, sample_price in reversed(samples):
            if sample_second <= cutoff:
                reference = sample_price
                break
        if not reference:
            return None
        return (samples[-1][1] / reference - 1.0) * 100

    def _update_momentum_price(self, market: str, second: int, price: float) -> None:
        samples = self._momentum_prices[market]
        sample_second = second - second % 5
        if not samples or samples[-1][0] != sample_second:
            samples.append((sample_second, price))
        else:
            samples[-1] = (sample_second, price)
        while samples and samples[0][0] < second - 3720:
            samples.popleft()

    def _in_preleader_window(self, now: int) -> bool:
        if not self.config.preleader_enabled:
            return False
        local = datetime.fromtimestamp(now, tz=_KST)
        minute_of_day = local.hour * 60 + local.minute
        start = self.config.preleader_start_minute_kst
        end = self.config.preleader_end_minute_kst
        if start <= end:
            return start <= minute_of_day <= end
        return minute_of_day >= start or minute_of_day <= end

    @staticmethod
    def _completed_minute_values(
        window: deque[_SecondBucket], now: int, minutes: int
    ) -> list[float]:
        boundary = now - now % 60
        totals: dict[int, float] = defaultdict(float)
        earliest = boundary - minutes * 60
        for bucket in window:
            if earliest <= bucket.second < boundary:
                index = (boundary - 1 - bucket.second) // 60
                totals[index] += bucket.trade_value
        return [totals[index] for index in range(minutes) if index in totals]

    def _rank_improvement(
        self, market: str, now: int, percentile: float
    ) -> float:
        history = self._relative_rank_history[market]
        if not history or now - history[-1][0] >= 30:
            history.append((now, percentile))
        while history and history[0][0] < now - 600:
            history.popleft()
        reference = next(
            (
                prior_percentile
                for observed_at, prior_percentile in reversed(history)
                if observed_at <= now - 180
            ),
            None,
        )
        return max(0.0, float(reference) - percentile) if reference else 0.0

    def relative_strength_snapshot(self, market: str, now: int) -> dict[str, Any]:
        """Rank the current market against fresh KRW-market rolling momentum."""
        if not self.config.relative_strength_enabled:
            return {"relative_strength_ready": False}

        snapshots: list[tuple[str, float, float, float | None, float | None]] = []
        for code, samples in self._momentum_prices.items():
            if (
                not samples
                or now - samples[-1][0]
                > self.config.relative_strength_stale_seconds
            ):
                continue
            change_5m = self._rolling_return(samples, now, 300)
            if change_5m is None:
                continue
            change_15m = self._rolling_return(samples, now, 900)
            change_60m = self._rolling_return(samples, now, 3600)
            weighted: list[tuple[float, float]] = [(change_5m, 0.55)]
            if change_15m is not None:
                weighted.append((change_15m, 0.30))
            if change_60m is not None:
                weighted.append((change_60m, 0.15))
            total_weight = sum(weight for _, weight in weighted)
            score = sum(value * weight for value, weight in weighted) / total_weight
            snapshots.append((code, score, change_5m, change_15m, change_60m))

        snapshots.sort(key=lambda item: item[1], reverse=True)
        universe = len(snapshots)
        ready = universe >= self.config.relative_strength_min_universe
        selected = next((item for item in snapshots if item[0] == market), None)
        if selected is None:
            return {
                "relative_strength_ready": ready,
                "relative_strength_universe": universe,
            }
        rank = snapshots.index(selected) + 1
        percentile = rank / universe * 100 if universe else 100.0
        _, score, change_5m, change_15m, change_60m = selected
        eligible = bool(
            ready
            and percentile <= self.config.relative_strength_top_percent
            and change_5m >= self.config.relative_strength_min_5m_pct
            and (change_15m is None or change_15m > 0)
        )
        return {
            "relative_strength_ready": ready,
            "relative_strength_eligible": eligible,
            "relative_strength_rank": rank,
            "relative_strength_universe": universe,
            "relative_strength_percentile": round(percentile, 2),
            "relative_strength_score": round(score, 3),
            "momentum_5m_pct": round(change_5m, 2),
            "momentum_15m_pct": (
                round(change_15m, 2) if change_15m is not None else None
            ),
            "momentum_60m_pct": (
                round(change_60m, 2) if change_60m is not None else None
            ),
            "early_trend": bool(
                eligible
                and change_5m >= self.config.relative_strength_min_5m_pct
                and (change_15m is None or change_15m < change_5m * 4)
            ),
        }

    def update(
        self, market: str, price: float, volume: float, timestamp_ms: int
    ) -> list[dict[str, Any]]:
        if price <= 0 or volume < 0:
            return []
        second = int(timestamp_ms / 1000)
        self._update_momentum_price(market, second, price)
        window = self._windows[market]
        trade_value = price * volume

        if window and window[-1].second == second:
            bucket = window[-1]
            bucket.high_price = max(bucket.high_price, price)
            bucket.low_price = min(bucket.low_price, price)
            bucket.closing_price = price
            bucket.trade_value += trade_value
        else:
            window.append(
                _SecondBucket(second, price, price, price, price, trade_value)
            )

        history_seconds = max(
            360,
            self.config.rebreakout_consolidation_seconds + 120,
            1920 if self.config.preleader_enabled else 0,
        )
        while window and window[0].second < second - history_seconds:
            window.popleft()

        if self._last_evaluated_second.get(market) == second:
            return []
        self._last_evaluated_second[market] = second
        return self._evaluate(market, window, second)

    def _evaluate(
        self, market: str, window: deque[_SecondBucket], now: int
    ) -> list[dict[str, Any]]:
        if not window or now - window[0].second < 110:
            return []

        current_price = window[-1].closing_price
        recent = [bucket for bucket in window if bucket.second >= now - 60]
        previous = [
            bucket for bucket in window if now - 120 <= bucket.second < now - 60
        ]
        if not recent or not previous:
            return []

        start_price = recent[0].opening_price
        change_1m_pct = ((current_price / start_price) - 1.0) * 100
        value_1m = sum(bucket.trade_value for bucket in recent)
        previous_value = sum(bucket.trade_value for bucket in previous)
        volume_ratio = value_1m / previous_value if previous_value > 0 else 0.0

        signals: list[tuple[str, str, dict[str, Any]]] = []
        relative_strength: dict[str, Any] = {}

        if (
            self._in_preleader_window(now)
            and now - self._last_preleader_checked_at.get(market, 0)
            >= self.config.preleader_check_interval_seconds
        ):
            self._last_preleader_checked_at[market] = now
            values_10m = self._completed_minute_values(window, now, 10)
            values_30m = self._completed_minute_values(window, now, 30)
            samples = self._momentum_prices[market]
            change_3m = self._rolling_return(samples, now, 180)
            change_5m = self._rolling_return(samples, now, 300)
            if (
                len(values_10m) >= 7
                and len(values_30m) >= 20
                and change_3m is not None
                and change_5m is not None
            ):
                baseline_10m = float(median(values_10m))
                baseline_30m = float(median(values_30m))
                ratio_10m = value_1m / baseline_10m if baseline_10m > 0 else 0.0
                ratio_30m = value_1m / baseline_30m if baseline_30m > 0 else 0.0
                relative_strength = self.relative_strength_snapshot(market, now)
                percentile = float(
                    relative_strength.get("relative_strength_percentile") or 100.0
                )
                rank_improvement = self._rank_improvement(
                    market, now, percentile
                )
                leadership_accelerating = bool(
                    relative_strength.get("relative_strength_ready")
                    and percentile <= self.config.preleader_max_percentile
                    and (
                        percentile <= self.config.relative_strength_top_percent
                        or rank_improvement
                        >= self.config.preleader_rank_improvement_pct
                    )
                )
                if (
                    value_1m >= self.config.min_trade_value_krw
                    and ratio_10m >= self.config.preleader_min_value_ratio_10m
                    and ratio_30m >= self.config.preleader_min_value_ratio_30m
                    and change_3m >= self.config.preleader_min_3m_pct
                    and change_5m >= self.config.preleader_min_5m_pct
                    and leadership_accelerating
                    and now - self._last_preleader_at.get(market, 0)
                    >= self.config.preleader_cooldown_seconds
                ):
                    self._last_preleader_at[market] = now
                    signals.append(
                        (
                            "leader_volume_acceleration",
                            "09시 전후 거래대금 선행 가속",
                            {
                                "internal_only": True,
                                "momentum_3m_pct": round(change_3m, 2),
                                "preleader_volume_ratio_10m": round(ratio_10m, 2),
                                "preleader_volume_ratio_30m": round(ratio_30m, 2),
                                "preleader_rank_improvement_pct": round(
                                    rank_improvement, 2
                                ),
                            },
                        )
                    )

        # Prefer a renewed breakout after a long, narrow consolidation over the
        # shorter generic surge signals. The most recent minute is excluded from
        # the baseline so its volume can be compared with the preceding range.
        if (
            self.config.rebreakout_enabled
            and now - window[0].second
            >= self.config.rebreakout_consolidation_seconds + 55
        ):
            consolidation_start = (
                now - self.config.rebreakout_consolidation_seconds - 60
            )
            consolidation = [
                bucket
                for bucket in window
                if consolidation_start <= bucket.second < now - 60
            ]
            if len(consolidation) >= 30:
                range_high = max(bucket.high_price for bucket in consolidation)
                range_low = min(bucket.low_price for bucket in consolidation)
                range_pct = (
                    ((range_high / range_low) - 1.0) * 100 if range_low > 0 else 0.0
                )
                baseline_value = sum(bucket.trade_value for bucket in consolidation)
                baseline_value_1m = (
                    baseline_value * 60 / (self.config.rebreakout_consolidation_seconds)
                )
                rebreakout_volume_ratio = (
                    value_1m / baseline_value_1m if baseline_value_1m > 0 else 0.0
                )
                rebreakout_level = range_high * (
                    1 + self.config.rebreakout_price_buffer_pct / 100
                )
                if (
                    range_pct <= self.config.rebreakout_max_range_pct
                    and current_price >= rebreakout_level
                    and change_1m_pct >= self.config.rebreakout_price_buffer_pct
                    and value_1m >= self.config.min_trade_value_krw
                    and rebreakout_volume_ratio
                    >= self.config.rebreakout_min_volume_ratio
                ):
                    minutes = int(self.config.rebreakout_consolidation_seconds / 60)
                    signals.append(
                        (
                            "consolidation_rebreakout",
                            f"{minutes}분 횡보 후 거래량 재돌파",
                            {
                                "consolidation_minutes": minutes,
                                "consolidation_range_pct": round(range_pct, 2),
                                "consolidation_high": range_high,
                                "breakout_level": rebreakout_level,
                                "volume_ratio_vs_consolidation": round(
                                    rebreakout_volume_ratio, 2
                                ),
                            },
                        )
                    )

        if (
            change_1m_pct >= self.config.price_surge_1m_pct
            and value_1m >= self.config.min_trade_value_krw
            and volume_ratio >= self.config.volume_ratio
        ):
            signals.append(("price_volume_surge", "가격·거래대금 급증", {}))

        if (
            change_1m_pct <= -self.config.price_surge_1m_pct
            and value_1m >= self.config.min_trade_value_krw
            and volume_ratio >= self.config.volume_ratio
        ):
            signals.append(("rapid_drop", "단기 급락 위험", {}))

        prior = [bucket for bucket in window if now - 300 <= bucket.second < now - 10]
        if prior and now - window[0].second >= 180:
            prior_high = max(bucket.high_price for bucket in prior)
            breakout_level = prior_high * (1 + self.config.breakout_pct / 100)
            if (
                current_price >= breakout_level
                and change_1m_pct > 0
                and value_1m >= self.config.min_trade_value_krw
                and volume_ratio >= max(1.5, self.config.volume_ratio * 0.75)
            ):
                signals.append(
                    (
                        "breakout",
                        "5분 고점 돌파",
                        {"prior_high": prior_high, "breakout_level": breakout_level},
                    )
                )

        if signals and not relative_strength:
            relative_strength = self.relative_strength_snapshot(market, now)
        alerts = []
        for signal_type, label, details in signals:
            if (
                signal_type != "leader_volume_acceleration"
                and not self._can_alert(market, signal_type, now)
            ):
                continue
            alerts.append(
                {
                    "time_utc": datetime.fromtimestamp(
                        now, tz=timezone.utc
                    ).isoformat(),
                    "market": market,
                    "signal": signal_type,
                    "label": label,
                    "price": current_price,
                    "change_1m_pct": round(change_1m_pct, 2),
                    "trade_value_1m_krw": round(value_1m),
                    "volume_ratio_vs_previous_1m": round(volume_ratio, 2),
                    **relative_strength,
                    **details,
                }
            )
        return alerts

    def _can_alert(self, market: str, signal_type: str, now: int) -> bool:
        key = (market, signal_type)
        if now - self._last_alert_at.get(key, 0) < self.config.alert_cooldown_seconds:
            return False
        while self._global_alerts and self._global_alerts[0] <= now - 60:
            self._global_alerts.popleft()
        if len(self._global_alerts) >= self.config.max_alerts_per_minute:
            return False
        self._last_alert_at[key] = now
        self._global_alerts.append(now)
        return True


def _format_price(price: float) -> str:
    if price >= 100:
        return f"{price:,.0f}원"
    return f"{price:,.8f}".rstrip("0").rstrip(".") + "원"


def _alert_text(alert: dict[str, Any]) -> str:
    extra = ""
    if alert.get("signal") == "consolidation_rebreakout":
        extra = (
            f"횡보 구간: {alert['consolidation_minutes']}분 / "
            f"가격폭 {alert['consolidation_range_pct']:.2f}%\n"
            f"횡보 평균 대비 1분 거래대금: "
            f"{alert['volume_ratio_vs_consolidation']:.2f}배\n"
        )
    return (
        f"[업비트 실시간 감시] {alert['market']} {alert['label']}\n"
        f"현재가: {_format_price(float(alert['price']))}\n"
        f"1분 등락: {alert['change_1m_pct']:+.2f}%\n"
        f"1분 거래대금: {alert['trade_value_1m_krw']:,.0f}원\n"
        f"직전 1분 대비 거래대금: {alert['volume_ratio_vs_previous_1m']:.2f}배\n"
        f"{extra}"
        "자동 주문 신호가 아니라 관찰 알림입니다. 호가와 지지·저항을 확인하세요."
    )


def _candidate_text(candidate: dict[str, Any]) -> str:
    reasons = "·".join(candidate.get("reasons", [])) or "공개 시세 조건 충족"
    risk_notes = "·".join(candidate.get("risk_notes", []))
    risk_line = f"위험 감점: {risk_notes}\n" if risk_notes else ""
    labels = []
    if candidate.get("leader_pullback_recheck"):
        labels.append("선도주 눌림")
    elif candidate.get("is_reentry"):
        labels.append("재지지")
    if candidate.get("availability_tier"):
        labels.append("보완형")
    suffix = f" | {'·'.join(labels)}" if labels else ""
    relative_line = ""
    if candidate.get("relative_strength_ready"):
        relative_line = (
            "상대강도: "
            f"{int(candidate.get('relative_strength_rank') or 0)}/"
            f"{int(candidate.get('relative_strength_universe') or 0)}위 · "
            f"5분 {float(candidate.get('momentum_5m_pct') or 0):+.1f}%"
        )
        if candidate.get("momentum_15m_pct") is not None:
            relative_line += (
                f" · 15분 {float(candidate['momentum_15m_pct']):+.1f}%"
            )
        relative_line += "\n"
    extension_line = ""
    if candidate.get("target_3") and candidate.get("target_4"):
        extension_line = (
            f"추세 확장 관찰: {_format_price(float(candidate['target_3']))} "
            f"(+{float(candidate['target_3_pct']):.0f}%) · "
            f"{_format_price(float(candidate['target_4']))} "
            f"(+{float(candidate['target_4_pct']):.0f}%)\n"
        )
    return (
        f"[조건부 진입 후보{suffix} | 조건점수 {candidate['score']}/100] "
        f"{candidate['market']}\n"
        f"현재가: {_format_price(float(candidate['current_price']))}\n"
        f"진입구간: {_format_price(float(candidate['entry_low']))} ~ "
        f"{_format_price(float(candidate['entry_high']))}\n"
        f"추격금지: {_format_price(float(candidate['chase_limit']))} 이상\n"
        f"손절가: {_format_price(float(candidate['stop_price']))}\n"
        f"1차 목표: {_format_price(float(candidate['target_1']))} "
        f"(약 +{candidate['target_1_pct']:.1f}%)\n"
        f"2차 목표: {_format_price(float(candidate['target_2']))} "
        f"(약 +{candidate['target_2_pct']:.1f}%)\n"
        f"{extension_line}"
        f"목표 방식: {candidate['target_mode']}\n"
        f"추세 관리: {candidate.get('trend_management', '고정 목표 관리')}\n"
        f"{relative_line}"
        f"가까운 저항 여유: {candidate.get('resistance_room_pct', 0):.1f}%\n"
        f"예상 손익비: {candidate.get('risk_reward', 0):.2f}\n"
        f"추천 비중: 투자 가능금액의 {candidate['suggested_position_pct']}% 이내\n"
        f"신호 유효시간: {int(candidate['valid_seconds'] / 60)}분\n"
        f"{risk_line}"
        f"선정 근거: {reasons}\n"
        "조건점수는 적중 확률이 아닙니다. 자동 주문이나 수익 보장이 아닌 "
        "공개 시세 기반 조건부 관찰 정보입니다."
    )


def _revalidate_candidate_for_dispatch(
    candidate: dict[str, Any], live_price: float
) -> tuple[dict[str, Any] | None, str | None]:
    """Keep Telegram counts meaningful by sending only actionable entries."""
    entry_low = float(candidate["entry_low"])
    entry_high = float(candidate["entry_high"])
    chase_limit = float(candidate["chase_limit"])
    stop = float(candidate["stop_price"])
    target_1 = float(candidate["target_1"])
    target_2 = float(candidate["target_2"])
    tolerance = max(abs(entry_high) * 1e-9, 1e-12)
    if live_price < entry_low - tolerance or live_price > entry_high + tolerance:
        return None, (
            f"전송 직전 현재가가 진입구간 밖({live_price:g}원, "
            f"{entry_low:g}~{entry_high:g}원)"
        )
    if live_price >= chase_limit:
        return None, f"전송 직전 추격금지선 도달({live_price:g}원)"
    if live_price <= stop or live_price >= target_1:
        return None, f"전송 전 손절·1차 목표 구간 도달({live_price:g}원)"

    refreshed = dict(candidate)
    risk = live_price - stop
    resistance = float(candidate.get("resistance_price") or target_1)
    refreshed.update(
        {
            "current_price": live_price,
            "entry_reference_price": live_price,
            "target_1_pct": round((target_1 / live_price - 1) * 100, 2),
            "target_2_pct": round((target_2 / live_price - 1) * 100, 2),
            "resistance_room_pct": round((resistance / live_price - 1) * 100, 2),
            "risk_reward": round(
                (resistance - live_price) / risk if risk > 0 else 0.0, 2
            ),
        }
    )
    return refreshed, None


class AlertDispatcher:
    def __init__(self) -> None:
        self._telegram_token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
        self._telegram_chat_id = os.getenv("TELEGRAM_CHAT_ID", "").strip()
        self._send_observation_alerts = _enabled(
            "TELEGRAM_SEND_OBSERVATION_ALERTS", True
        )
        self._send_candidate_alerts = _enabled("TELEGRAM_SEND_CANDIDATE_ALERTS", True)
        self._candidate_min_target_2_pct = max(
            0.0, _env_float("TELEGRAM_CANDIDATE_MIN_TARGET_2_PCT", 5.0)
        )
        self._candidate_min_score = max(
            0, min(100, _env_int("TELEGRAM_CANDIDATE_MIN_SCORE", 90))
        )
        self._daily_candidate_min = max(
            0, min(10, _env_int("TELEGRAM_DAILY_CANDIDATE_MIN", 5))
        )
        self._daily_candidate_max = max(
            self._daily_candidate_min,
            min(20, _env_int("TELEGRAM_DAILY_CANDIDATE_MAX", 10)),
        )
        self._availability_min_interval_seconds = max(
            900,
            _env_int("TELEGRAM_AVAILABILITY_MIN_INTERVAL_SECONDS", 5400),
        )
        self._send_inactivity_status = _enabled(
            "TELEGRAM_SEND_INACTIVITY_STATUS", True
        )
        self._inactivity_status_seconds = max(
            1800, _env_int("TELEGRAM_INACTIVITY_STATUS_SECONDS", 3600)
        )
        now = time.monotonic()
        self._last_candidate_delivery_at = now
        self._last_inactivity_status_at = now
        self._candidate_delivery_day = self._today_kst()
        self._candidate_delivery_count = 0
        self._last_availability_delivery_at = 0.0
        self._raw_signal_times: deque[float] = deque(maxlen=5000)
        self._candidate_rejections: deque[tuple[float, tuple[str, ...]]] = deque(
            maxlen=5000
        )
        self._publish_candidate_delivery_state()

    @staticmethod
    def _today_kst() -> str:
        return datetime.now(_KST).date().isoformat()

    def _refresh_candidate_delivery_day(self) -> None:
        day = self._today_kst()
        if day == self._candidate_delivery_day:
            return
        self._candidate_delivery_day = day
        self._candidate_delivery_count = 0
        self._last_availability_delivery_at = 0.0
        self._publish_candidate_delivery_state()

    def _publish_candidate_delivery_state(self, *, delivered: bool = False) -> None:
        MONITOR_STATE.update_candidate_delivery(
            day_kst=self._candidate_delivery_day,
            count=self._candidate_delivery_count,
            minimum=self._daily_candidate_min,
            maximum=self._daily_candidate_max,
            delivered=delivered,
        )

    @property
    def mode(self) -> str:
        if self._telegram_token and self._telegram_chat_id:
            return "telegram"
        return "log_only"

    @property
    def observation_delivery_enabled(self) -> bool:
        return self._send_observation_alerts

    @property
    def candidate_delivery_enabled(self) -> bool:
        return self._send_candidate_alerts

    @property
    def candidate_min_target_2_pct(self) -> float:
        return self._candidate_min_target_2_pct

    @property
    def candidate_min_score(self) -> int:
        return self._candidate_min_score

    @property
    def daily_candidate_min(self) -> int:
        return self._daily_candidate_min

    @property
    def daily_candidate_max(self) -> int:
        return self._daily_candidate_max

    @property
    def inactivity_status_enabled(self) -> bool:
        return self._send_inactivity_status

    @property
    def inactivity_status_seconds(self) -> int:
        return self._inactivity_status_seconds

    def record_candidate_rejection(self, reasons: list[str]) -> None:
        self._candidate_rejections.append((time.monotonic(), tuple(reasons)))

    def _prune_activity(self, now: float) -> None:
        cutoff = now - self._inactivity_status_seconds
        while self._raw_signal_times and self._raw_signal_times[0] < cutoff:
            self._raw_signal_times.popleft()
        while self._candidate_rejections and self._candidate_rejections[0][0] < cutoff:
            self._candidate_rejections.popleft()

    @staticmethod
    def _reason_label(reason: str) -> str:
        return reason.split("(", 1)[0].strip()

    def _inactivity_status_text(self, now: float) -> str:
        self._prune_activity(now)
        reason_counts = Counter(
            self._reason_label(reason)
            for _, reasons in self._candidate_rejections
            for reason in reasons
        )
        top_reasons = " · ".join(
            f"{reason} {count}건" for reason, count in reason_counts.most_common(3)
        ) or "심층검증 대상 없음"
        status = MONITOR_STATE.snapshot()
        connection = "정상" if status["connected"] else "재연결 중"
        minutes = max(1, self._inactivity_status_seconds // 60)
        return (
            f"[운영상태 | 최근 {minutes}분]\n"
            f"공개 시세 감시: {connection}\n"
            f"원시 상승신호: {len(self._raw_signal_times)}건\n"
            f"심층검증 탈락: {len(self._candidate_rejections)}건\n"
            f"주요 탈락 사유: {top_reasons}\n"
            "조건부 진입 후보: 0건\n"
            "서비스는 계속 감시 중이며, 이 메시지는 매수 신호가 아닙니다."
        )

    async def send_inactivity_status_if_due(self) -> bool:
        if (
            self.mode != "telegram"
            or not self._send_candidate_alerts
            or not self._send_inactivity_status
        ):
            return False
        now = time.monotonic()
        if (
            now - self._last_candidate_delivery_at < self._inactivity_status_seconds
            or now - self._last_inactivity_status_at < self._inactivity_status_seconds
        ):
            return False
        # Throttle retries even when Telegram is temporarily unavailable.
        self._last_inactivity_status_at = now
        url = f"https://api.telegram.org/bot{self._telegram_token}/sendMessage"
        async with httpx.AsyncClient(timeout=10, follow_redirects=False) as client:
            response = await client.post(
                url,
                json={
                    "chat_id": self._telegram_chat_id,
                    "text": self._inactivity_status_text(now),
                    "disable_web_page_preview": True,
                },
            )
            response.raise_for_status()
        LOGGER.info("Telegram inactivity status delivered")
        return True

    async def run_inactivity_status_loop(self) -> None:
        while True:
            await asyncio.sleep(60)
            try:
                await self.send_inactivity_status_if_due()
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                LOGGER.error("Inactivity status delivery failed: %s", exc)

    async def send(self, alert: dict[str, Any]) -> None:
        if alert.get("signal") in {
            "price_volume_surge",
            "breakout",
            "consolidation_rebreakout",
        }:
            self._raw_signal_times.append(time.monotonic())
        MONITOR_STATE.add_alert(alert)
        LOGGER.warning("MARKET_ALERT %s", json.dumps(alert, ensure_ascii=False))
        if self.mode != "telegram" or not self._send_observation_alerts:
            return
        url = f"https://api.telegram.org/bot{self._telegram_token}/sendMessage"
        async with httpx.AsyncClient(timeout=10, follow_redirects=False) as client:
            response = await client.post(
                url,
                json={
                    "chat_id": self._telegram_chat_id,
                    "text": _alert_text(alert),
                    "disable_web_page_preview": True,
                },
            )
            response.raise_for_status()

    async def send_candidate(self, candidate: dict[str, Any]) -> bool:
        LOGGER.warning("ENTRY_CANDIDATE %s", json.dumps(candidate, ensure_ascii=False))
        if self.mode != "telegram" or not self._send_candidate_alerts:
            return False
        score = int(candidate.get("score", 0))
        target_2_pct = float(candidate.get("target_2_pct", 0.0))
        if (
            score < self._candidate_min_score
            or target_2_pct < self._candidate_min_target_2_pct
        ):
            LOGGER.info(
                "ENTRY_CANDIDATE_TELEGRAM_SUPPRESSED market=%s "
                "score=%d minimum_score=%d target_2_pct=%.2f "
                "minimum_target_2_pct=%.2f",
                candidate.get("market"),
                score,
                self._candidate_min_score,
                target_2_pct,
                self._candidate_min_target_2_pct,
            )
            return False
        self._refresh_candidate_delivery_day()
        availability_tier = bool(candidate.get("availability_tier"))
        now = time.monotonic()
        if self._candidate_delivery_count >= self._daily_candidate_max:
            LOGGER.info(
                "ENTRY_CANDIDATE_DAILY_MAX_SUPPRESSED market=%s count=%d max=%d",
                candidate.get("market"),
                self._candidate_delivery_count,
                self._daily_candidate_max,
            )
            return False
        if (
            availability_tier
            and self._candidate_delivery_count >= self._daily_candidate_min
        ):
            LOGGER.info(
                "ENTRY_CANDIDATE_AVAILABILITY_TARGET_REACHED market=%s count=%d target=%d",
                candidate.get("market"),
                self._candidate_delivery_count,
                self._daily_candidate_min,
            )
            return False
        if (
            availability_tier
            and self._last_availability_delivery_at
            and now - self._last_availability_delivery_at
            < self._availability_min_interval_seconds
        ):
            LOGGER.info(
                "ENTRY_CANDIDATE_AVAILABILITY_PACED market=%s remaining_seconds=%d",
                candidate.get("market"),
                int(
                    self._availability_min_interval_seconds
                    - (now - self._last_availability_delivery_at)
                ),
            )
            return False

        # Reserve the slot before the network await so concurrent analyses
        # cannot exceed the daily maximum. Roll it back if Telegram fails.
        self._candidate_delivery_count += 1
        if availability_tier:
            self._last_availability_delivery_at = now
        url = f"https://api.telegram.org/bot{self._telegram_token}/sendMessage"
        try:
            async with httpx.AsyncClient(timeout=10, follow_redirects=False) as client:
                response = await client.post(
                    url,
                    json={
                        "chat_id": self._telegram_chat_id,
                        "text": _candidate_text(candidate),
                        "disable_web_page_preview": True,
                    },
                )
                response.raise_for_status()
        except Exception:
            self._candidate_delivery_count -= 1
            if availability_tier:
                self._last_availability_delivery_at = 0.0
            self._publish_candidate_delivery_state()
            raise
        MONITOR_STATE.add_alert(candidate)
        self._last_candidate_delivery_at = time.monotonic()
        self._last_inactivity_status_at = self._last_candidate_delivery_at
        self._publish_candidate_delivery_state(delivered=True)
        return True


class CandidateAnalyzer:
    """Confirm positive WebSocket signals with fresh REST market snapshots."""

    def __init__(
        self,
        config: CandidateConfig,
        dispatcher: AlertDispatcher,
        relative_strength_provider: Callable[[str, int], dict[str, Any]] | None = None,
    ) -> None:
        self.config = config
        self.dispatcher = dispatcher
        self._relative_strength_provider = relative_strength_provider
        self._last_checked_at: dict[str, float] = {}
        self._last_reentry_at: dict[str, float] = {}
        self._last_leader_recheck_at: dict[str, float] = {}
        self._lifecycles: dict[str, dict[str, Any]] = {}
        self._watchlist: dict[str, dict[str, Any]] = {}
        self._signal_tracks: dict[str, dict[str, Any]] = {}
        self._trend_tracks: dict[str, dict[str, Any]] = {}
        self._inflight_markets: set[str] = set()
        self._tasks: set[asyncio.Task[None]] = set()
        self._semaphore = asyncio.Semaphore(2)

    def _active_watch(self, market: str, now: float) -> dict[str, Any] | None:
        state = self._watchlist.get(market)
        if state and now >= float(state["expires_at"]):
            self._watchlist.pop(market, None)
            return None
        return state

    def watch_preleader(self, alert: dict[str, Any]) -> bool:
        """Keep an early volume leader internal until a clean retest occurs."""
        if not self.config.enabled or not self.config.leader_watch_enabled:
            return False
        if alert.get("signal") != "leader_volume_acceleration":
            return False
        market = str(alert["market"])
        now = time.time()
        state = self._active_watch(market, now)
        created = state is None
        if state is None:
            state = {
                "market": market,
                "created_at": now,
                "first_signal_time_utc": alert.get("time_utc"),
                "first_signal_price": float(alert["price"]),
                "recheck_count": 0,
                "leader_recheck_count": 0,
            }
        price = float(alert["price"])
        state.update(
            {
                "source_signal": alert["signal"],
                "breakout_level": price,
                "last_price": price,
                "last_rejected": ["09시 전후 선도주 조기탐지 후 첫 눌림 대기"],
                "last_checked_at": now,
                "expires_at": float(state["created_at"])
                + self.config.watchlist_window_seconds,
                "leader_peak_price": max(
                    float(state.get("leader_peak_price") or 0), price
                ),
                "leader_pullback_low": float(
                    state.get("leader_pullback_low") or price
                ),
                "leader_pullback_seen": bool(
                    state.get("leader_pullback_seen", False)
                ),
                "leader_pullback_invalidated": bool(
                    state.get("leader_pullback_invalidated", False)
                ),
                "relative_strength_rank": alert.get("relative_strength_rank"),
                "relative_strength_universe": alert.get(
                    "relative_strength_universe"
                ),
                "relative_strength_percentile": alert.get(
                    "relative_strength_percentile"
                ),
                "momentum_3m_pct": alert.get("momentum_3m_pct"),
                "momentum_5m_pct": alert.get("momentum_5m_pct"),
                "preleader_volume_ratio_10m": alert.get(
                    "preleader_volume_ratio_10m"
                ),
                "preleader_volume_ratio_30m": alert.get(
                    "preleader_volume_ratio_30m"
                ),
            }
        )
        self._watchlist[market] = state
        self._start_signal_track(alert, now)
        MONITOR_STATE.add_alert(alert)
        MONITOR_STATE.add_screening_record(
            self._screening_record(alert, "preleader_watch", [])
        )
        LOGGER.info(
            "Preleader retained for first pullback: %s rank=%s/%s "
            "volume10m=%sx volume30m=%sx",
            market,
            alert.get("relative_strength_rank"),
            alert.get("relative_strength_universe"),
            alert.get("preleader_volume_ratio_10m"),
            alert.get("preleader_volume_ratio_30m"),
        )
        return created

    def _start_signal_track(self, alert: dict[str, Any], now: float) -> None:
        """Track +5% versus -3% first-touch outcomes for every screened signal."""
        market = str(alert["market"])
        existing = self._signal_tracks.get(market)
        if existing and now < float(existing["expires_at"]):
            return
        price = float(alert["price"])
        self._signal_tracks[market] = {
            "market": market,
            "source_signal": alert["signal"],
            "signal_price": price,
            "target_price": price * (1 + self.config.raw_signal_target_pct / 100),
            "stop_price": price * (1 - self.config.raw_signal_stop_pct / 100),
            "target_pct": self.config.raw_signal_target_pct,
            "stop_pct": self.config.raw_signal_stop_pct,
            "max_price": price,
            "min_price": price,
            "created_at": now,
            "signal_time_utc": alert.get("time_utc"),
            "expires_at": now + self.config.watchlist_window_seconds,
            "approved_candidate": False,
            "relative_strength_rank": alert.get("relative_strength_rank"),
            "relative_strength_universe": alert.get("relative_strength_universe"),
            "relative_strength_percentile": alert.get(
                "relative_strength_percentile"
            ),
            "momentum_5m_pct": alert.get("momentum_5m_pct"),
            "momentum_15m_pct": alert.get("momentum_15m_pct"),
            "momentum_60m_pct": alert.get("momentum_60m_pct"),
            "early_trend": alert.get("early_trend"),
        }

    def _finish_signal_track(
        self,
        market: str,
        state: dict[str, Any],
        result: str,
        exit_price: float,
        now: float,
    ) -> None:
        entry = float(state["signal_price"])
        MONITOR_STATE.add_signal_outcome(
            {
                "market": market,
                "source_signal": state["source_signal"],
                "result": result,
                "approved_candidate": bool(state["approved_candidate"]),
                "signal_price": entry,
                "exit_price": exit_price,
                "target_price": state["target_price"],
                "stop_price": state["stop_price"],
                "mfe_pct": round((float(state["max_price"]) / entry - 1) * 100, 2),
                "mae_pct": round((float(state["min_price"]) / entry - 1) * 100, 2),
                "elapsed_seconds": round(now - float(state["created_at"]), 1),
                "signal_time_utc": state.get("signal_time_utc"),
                "completed_at_utc": datetime.now(timezone.utc).isoformat(),
                "relative_strength_rank": state.get("relative_strength_rank"),
                "relative_strength_universe": state.get(
                    "relative_strength_universe"
                ),
                "relative_strength_percentile": state.get(
                    "relative_strength_percentile"
                ),
                "momentum_5m_pct": state.get("momentum_5m_pct"),
                "momentum_15m_pct": state.get("momentum_15m_pct"),
                "momentum_60m_pct": state.get("momentum_60m_pct"),
                "early_trend": state.get("early_trend"),
            }
        )
        self._signal_tracks.pop(market, None)

    @staticmethod
    def _screening_record(
        alert: dict[str, Any], decision: str, reasons: list[str]
    ) -> dict[str, Any]:
        return {
            "time_utc": datetime.now(timezone.utc).isoformat(),
            "market": alert.get("market"),
            "source_signal": alert.get("signal"),
            "decision": decision,
            "reasons": list(reasons),
            "relative_strength_rank": alert.get("relative_strength_rank"),
            "relative_strength_universe": alert.get("relative_strength_universe"),
            "relative_strength_percentile": alert.get(
                "relative_strength_percentile"
            ),
            "momentum_5m_pct": alert.get("momentum_5m_pct"),
            "momentum_15m_pct": alert.get("momentum_15m_pct"),
            "momentum_60m_pct": alert.get("momentum_60m_pct"),
            "early_trend": alert.get("early_trend"),
            "change_1m_pct": alert.get("change_1m_pct"),
            "volume_ratio_vs_previous_1m": alert.get(
                "volume_ratio_vs_previous_1m"
            ),
        }

    def _start_trend_track(self, candidate: dict[str, Any], now: float) -> None:
        market = str(candidate["market"])
        entry = float(candidate["entry_reference_price"])
        self._trend_tracks[market] = {
            "market": market,
            "source_signal": candidate["source_signal"],
            "entry_price": entry,
            "stop_price": float(candidate["stop_price"]),
            "target_1": float(candidate["target_1"]),
            "target_2": float(candidate["target_2"]),
            "target_3": (
                float(candidate["target_3"]) if candidate.get("target_3") else None
            ),
            "target_4": (
                float(candidate["target_4"]) if candidate.get("target_4") else None
            ),
            "target_1_reached": False,
            "target_2_reached": False,
            "target_3_reached": False,
            "target_4_reached": False,
            "max_price": entry,
            "min_price": entry,
            "created_at": now,
            "expires_at": now + self.config.trend_tracking_seconds,
            "score": candidate["score"],
            "target_mode": candidate["target_mode"],
            "relative_strength_rank": candidate.get("relative_strength_rank"),
            "relative_strength_universe": candidate.get(
                "relative_strength_universe"
            ),
            "relative_strength_percentile": candidate.get(
                "relative_strength_percentile"
            ),
            "momentum_5m_pct": candidate.get("momentum_5m_pct"),
            "momentum_15m_pct": candidate.get("momentum_15m_pct"),
            "momentum_60m_pct": candidate.get("momentum_60m_pct"),
        }

    def _finish_trend_track(
        self,
        market: str,
        state: dict[str, Any],
        result: str,
        exit_price: float,
        now: float,
    ) -> None:
        entry = float(state["entry_price"])
        MONITOR_STATE.add_trend_outcome(
            {
                "market": market,
                "source_signal": state["source_signal"],
                "result": result,
                "score": state["score"],
                "target_mode": state["target_mode"],
                "entry_price": entry,
                "exit_price": exit_price,
                "target_1": state["target_1"],
                "target_2": state["target_2"],
                "target_3": state.get("target_3"),
                "target_4": state.get("target_4"),
                "stop_price": state["stop_price"],
                "target_1_reached": bool(state["target_1_reached"]),
                "target_2_reached": bool(state.get("target_2_reached")),
                "target_3_reached": bool(state.get("target_3_reached")),
                "target_4_reached": bool(state.get("target_4_reached")),
                "mfe_pct": round((float(state["max_price"]) / entry - 1) * 100, 2),
                "mae_pct": round((float(state["min_price"]) / entry - 1) * 100, 2),
                "elapsed_seconds": round(now - float(state["created_at"]), 1),
                "completed_at_utc": datetime.now(timezone.utc).isoformat(),
                "relative_strength_rank": state.get("relative_strength_rank"),
                "relative_strength_universe": state.get(
                    "relative_strength_universe"
                ),
                "relative_strength_percentile": state.get(
                    "relative_strength_percentile"
                ),
                "momentum_5m_pct": state.get("momentum_5m_pct"),
                "momentum_15m_pct": state.get("momentum_15m_pct"),
                "momentum_60m_pct": state.get("momentum_60m_pct"),
            }
        )
        self._trend_tracks.pop(market, None)

    def _observe_trend_track(self, market: str, price: float, now: float) -> None:
        state = self._trend_tracks.get(market)
        if not state:
            return
        state["max_price"] = max(float(state["max_price"]), price)
        state["min_price"] = min(float(state["min_price"]), price)
        target_3 = state.get("target_3")
        target_4 = state.get("target_4")
        if target_4 is not None and price >= float(target_4):
            state["target_1_reached"] = True
            state["target_2_reached"] = True
            state["target_3_reached"] = True
            state["target_4_reached"] = True
            self._finish_trend_track(market, state, "target_4_reached", price, now)
            return
        if target_3 is not None and price >= float(target_3):
            state["target_1_reached"] = True
            state["target_2_reached"] = True
            state["target_3_reached"] = True
        if price >= float(state["target_2"]):
            state["target_1_reached"] = True
            state["target_2_reached"] = True
            if target_4 is None:
                self._finish_trend_track(
                    market, state, "target_2_reached", price, now
                )
                return
        if price >= float(state["target_1"]):
            state["target_1_reached"] = True
        protected_stop = (
            float(state["entry_price"])
            if state["target_1_reached"]
            else float(state["stop_price"])
        )
        if price <= protected_stop:
            result = (
                "protected_after_target_1"
                if state["target_1_reached"]
                else "stop_before_target_1"
            )
            self._finish_trend_track(market, state, result, price, now)
        elif now >= float(state["expires_at"]):
            self._finish_trend_track(market, state, "expired", price, now)

    def _observe_signal_track(self, market: str, price: float, now: float) -> None:
        state = self._signal_tracks.get(market)
        if not state:
            return
        state["max_price"] = max(float(state["max_price"]), price)
        state["min_price"] = min(float(state["min_price"]), price)
        if price >= float(state["target_price"]):
            self._finish_signal_track(market, state, "target_first", price, now)
        elif price <= float(state["stop_price"]):
            self._finish_signal_track(market, state, "stop_first", price, now)
        elif now >= float(state["expires_at"]):
            self._finish_signal_track(market, state, "expired", price, now)

    def _remember_rejected(
        self,
        alert: dict[str, Any],
        rejected: list[str],
        current_price: float,
    ) -> None:
        market = str(alert["market"])
        now = time.time()
        state = self._active_watch(market, now)
        if state is None:
            state = {
                "market": market,
                "created_at": now,
                "first_signal_time_utc": alert.get("time_utc"),
                "first_signal_price": float(alert["price"]),
                "recheck_count": 0,
            }
        elif alert.get("watchlist_recheck"):
            state["recheck_count"] = int(state.get("recheck_count", 0)) + 1
        state.update(
            {
                "source_signal": alert["signal"],
                "breakout_level": float(
                    alert.get("breakout_level")
                    or alert.get("consolidation_high")
                    or alert["price"]
                ),
                "last_price": current_price,
                "last_rejected": list(rejected),
                "last_checked_at": now,
                "expires_at": float(state["created_at"])
                + self.config.watchlist_window_seconds,
                "leader_peak_price": max(
                    float(state.get("leader_peak_price") or 0),
                    float(alert["price"]),
                    current_price,
                ),
                "leader_pullback_low": float(
                    state.get("leader_pullback_low") or current_price
                ),
                "leader_pullback_seen": bool(
                    state.get("leader_pullback_seen", False)
                ),
                "leader_pullback_invalidated": bool(
                    state.get("leader_pullback_invalidated", False)
                ),
                "leader_recheck_count": int(
                    state.get("leader_recheck_count", 0)
                ),
                "relative_strength_rank": alert.get(
                    "relative_strength_rank",
                    state.get("relative_strength_rank"),
                ),
                "relative_strength_universe": alert.get(
                    "relative_strength_universe",
                    state.get("relative_strength_universe"),
                ),
                "relative_strength_percentile": alert.get(
                    "relative_strength_percentile",
                    state.get("relative_strength_percentile"),
                ),
            }
        )
        self._watchlist[market] = state
        LOGGER.info(
            "Candidate retained on 12h watchlist: %s rechecks=%d",
            market,
            state["recheck_count"],
        )

    def schedule(self, alert: dict[str, Any]) -> bool:
        if not self.config.enabled:
            return False
        if alert.get("signal") not in {
            "price_volume_surge",
            "breakout",
            "consolidation_rebreakout",
            "leader_volume_acceleration",
        }:
            return False
        market = str(alert["market"])
        now = time.time()
        watch = self._active_watch(market, now)
        watchlist_recheck = bool(
            watch and alert.get("signal") == "consolidation_rebreakout"
        )
        if market in self._inflight_markets:
            return False
        if (
            not watchlist_recheck
            and now - self._last_checked_at.get(market, 0) < self.config.cooldown_seconds
        ):
            return False
        if MONITOR_STATE.has_recent_signal(market, "rapid_drop", 600):
            LOGGER.info("Candidate skipped after recent rapid drop: %s", market)
            self.dispatcher.record_candidate_rejection(["최근 급락 발생"])
            return False

        scheduled = dict(alert)
        if watchlist_recheck:
            scheduled["is_reentry"] = True
            scheduled["watchlist_recheck"] = True
            scheduled["original_signal_time_utc"] = watch.get(
                "first_signal_time_utc"
            )
        self._start_signal_track(scheduled, now)
        self._last_checked_at[market] = now
        self._inflight_markets.add(market)
        task = asyncio.create_task(self._analyze(scheduled))
        self._tasks.add(task)

        def completed(done: asyncio.Task[None]) -> None:
            self._tasks.discard(done)
            self._inflight_markets.discard(market)

        task.add_done_callback(completed)
        return True

    def _observe_leader_watchlist(
        self, market: str, price: float, now: float
    ) -> bool:
        """Recheck a rejected market after a controlled leader pullback/reclaim."""
        if not self.config.leader_watch_enabled:
            return False
        state = self._active_watch(market, now)
        if not state or price <= 0:
            return False

        previous_peak = float(state.get("leader_peak_price") or price)
        if state.get("leader_pullback_invalidated"):
            if price <= previous_peak:
                return False
            state["leader_pullback_invalidated"] = False
            state["leader_pullback_seen"] = False
            state["leader_pullback_low"] = price

        peak = max(previous_peak, price)
        state["leader_peak_price"] = peak
        drawdown_pct = max(0.0, (peak / price - 1) * 100)
        max_pullback = max(
            self.config.leader_pullback_min_pct,
            self.config.leader_pullback_max_pct,
        )

        if drawdown_pct > max_pullback:
            # A deep slide invalidates the impulse. Do not call a later bounce
            # a first dip unless price establishes a genuinely fresh high.
            state["leader_pullback_invalidated"] = True
            state["leader_pullback_seen"] = False
            return False

        if not state.get("leader_pullback_seen"):
            if drawdown_pct < self.config.leader_pullback_min_pct:
                return False
            state["leader_pullback_seen"] = True
            state["leader_pullback_low"] = price
            return False

        pullback_low = min(float(state.get("leader_pullback_low") or price), price)
        state["leader_pullback_low"] = pullback_low
        reclaim_level = pullback_low * (1 + self.config.leader_reclaim_pct / 100)
        if price < reclaim_level:
            return False
        if market in self._inflight_markets:
            return False
        if MONITOR_STATE.has_recent_signal(market, "rapid_drop", 600):
            state["leader_pullback_seen"] = False
            LOGGER.info("Leader recheck skipped after recent rapid drop: %s", market)
            return False
        if (
            int(state.get("leader_recheck_count", 0))
            >= self.config.leader_watch_max_rechecks
        ):
            return False
        if (
            now - self._last_leader_recheck_at.get(market, 0)
            < self.config.leader_recheck_cooldown_seconds
        ):
            return False

        relative = (
            self._relative_strength_provider(market, int(now))
            if self._relative_strength_provider is not None
            else {
                "relative_strength_ready": False,
                "relative_strength_eligible": False,
            }
        )
        percentile = float(relative.get("relative_strength_percentile") or 100.0)
        still_a_leader = bool(
            relative.get("relative_strength_ready")
            and relative.get("relative_strength_eligible")
            and percentile <= self.config.relative_strength_top_percent
            and (
                not self.config.early_trend_required
                or relative.get("early_trend")
            )
        )
        if not still_a_leader:
            # A later fresh high can establish another pullback cycle while the
            # original raw signal remains on the 12-hour watchlist.
            if price >= peak:
                state["leader_pullback_seen"] = False
                state["leader_pullback_low"] = price
            return False

        self._last_leader_recheck_at[market] = now
        state["leader_recheck_count"] = int(
            state.get("leader_recheck_count", 0)
        ) + 1
        state["leader_pullback_seen"] = False
        state["leader_pullback_invalidated"] = False
        state["leader_pullback_low"] = price
        state["leader_peak_price"] = price
        alert = {
            "time_utc": datetime.now(timezone.utc).isoformat(),
            "market": market,
            "signal": state["source_signal"],
            "price": price,
            "breakout_level": reclaim_level,
            "is_reentry": True,
            "pullback_retest": True,
            "watchlist_recheck": True,
            "leader_pullback_recheck": True,
            "original_signal_time_utc": state.get("first_signal_time_utc"),
            **relative,
        }
        MONITOR_STATE.add_screening_record(
            self._screening_record(alert, "leader_recheck_scheduled", [])
        )
        self._inflight_markets.add(market)
        task = asyncio.create_task(self._analyze(alert))
        self._tasks.add(task)

        def completed(done: asyncio.Task[None]) -> None:
            self._tasks.discard(done)
            self._inflight_markets.discard(market)

        task.add_done_callback(completed)
        LOGGER.info(
            "Leader pullback recheck scheduled: %s pullback=%.2f%% "
            "reclaim=%.2f%% rank=%s/%s",
            market,
            drawdown_pct,
            (price / pullback_low - 1) * 100,
            relative.get("relative_strength_rank"),
            relative.get("relative_strength_universe"),
        )
        return True

    def observe_price(self, market: str, price: float) -> bool:
        """Schedule one fresh recheck after price leaves and retakes the entry zone."""
        now = time.time()
        self._observe_signal_track(market, price, now)
        self._observe_trend_track(market, price, now)
        leader_recheck_scheduled = self._observe_leader_watchlist(
            market, price, now
        )
        state = self._lifecycles.get(market)
        if not state:
            return leader_recheck_scheduled
        state["max_price"] = max(float(state["max_price"]), price)
        state["min_price"] = min(float(state["min_price"]), price)
        if price >= float(state["target_1"]):
            self._finish_lifecycle(market, state, "target_1_first", price, now)
            return leader_recheck_scheduled
        if price <= float(state["stop_price"]):
            self._finish_lifecycle(market, state, "stop_first", price, now)
            return leader_recheck_scheduled
        if now >= float(state["expires_at"]):
            self._finish_lifecycle(market, state, "expired", price, now)
            return leader_recheck_scheduled
        entry_low = float(state["entry_low"])
        entry_high = float(state["entry_high"])
        if price < entry_low or price > entry_high:
            state["waiting_retest"] = True
            return leader_recheck_scheduled
        if not state.get("waiting_retest") or not entry_low <= price <= entry_high:
            return leader_recheck_scheduled
        if market in self._inflight_markets:
            return leader_recheck_scheduled
        if (
            now - self._last_reentry_at.get(market, 0)
            < self.config.reentry_cooldown_seconds
        ):
            return leader_recheck_scheduled
        self._last_reentry_at[market] = now
        state["waiting_retest"] = False
        alert = {
            "time_utc": datetime.now(timezone.utc).isoformat(),
            "market": market,
            "signal": state["source_signal"],
            "price": price,
            "breakout_level": state["breakout_level"],
            "is_reentry": True,
            "consolidation_minutes": state.get("consolidation_minutes"),
            "relative_strength_ready": state.get("relative_strength_ready"),
            "relative_strength_eligible": state.get("relative_strength_eligible"),
            "relative_strength_rank": state.get("relative_strength_rank"),
            "relative_strength_universe": state.get("relative_strength_universe"),
            "relative_strength_percentile": state.get(
                "relative_strength_percentile"
            ),
            "momentum_5m_pct": state.get("momentum_5m_pct"),
            "momentum_15m_pct": state.get("momentum_15m_pct"),
            "momentum_60m_pct": state.get("momentum_60m_pct"),
        }
        if self._relative_strength_provider is not None:
            alert.update(self._relative_strength_provider(market, int(now)))
        self._inflight_markets.add(market)
        task = asyncio.create_task(self._analyze(alert))
        self._tasks.add(task)

        def completed(done: asyncio.Task[None]) -> None:
            self._tasks.discard(done)
            self._inflight_markets.discard(market)

        task.add_done_callback(completed)
        return True

    def _finish_lifecycle(
        self,
        market: str,
        state: dict[str, Any],
        result: str,
        exit_price: float,
        now: float,
    ) -> None:
        entry = float(state["entry_price"])
        MONITOR_STATE.add_candidate_outcome(
            {
                "market": market,
                "result": result,
                "score": state["score"],
                "is_reentry": state["is_reentry"],
                "entry_price": entry,
                "exit_price": exit_price,
                "target_1": state["target_1"],
                "stop_price": state["stop_price"],
                "mfe_pct": round((float(state["max_price"]) / entry - 1) * 100, 2),
                "mae_pct": round((float(state["min_price"]) / entry - 1) * 100, 2),
                "elapsed_seconds": round(now - float(state["created_at"]), 1),
                "completed_at_utc": datetime.now(timezone.utc).isoformat(),
            }
        )
        self._lifecycles.pop(market, None)

    async def _orderbook_samples(
        self, client: UpbitPublicClient, market: str
    ) -> list[dict[str, Any]]:
        samples: list[dict[str, Any]] = []
        for index in range(self.config.orderbook_sample_count):
            samples.append(await client.orderbook(market))
            if index + 1 < self.config.orderbook_sample_count:
                await asyncio.sleep(self.config.orderbook_sample_interval_seconds)
        return samples

    async def _analyze(self, alert: dict[str, Any]) -> None:
        market = str(alert["market"])
        try:
            seconds_to_next_close = 62 - (time.time() % 60)
            # If the signal arrived in the final 20 seconds of a candle, wait
            # for the following close. This avoids counting a candle that had
            # almost no post-signal trading while retaining faster confirmation
            # for signals detected earlier in the minute.
            if seconds_to_next_close < 22:
                seconds_to_next_close += 60
            await asyncio.sleep(max(self.config.confirm_seconds, seconds_to_next_close))
            async with self._semaphore:
                client = UpbitPublicClient()
                try:
                    (
                        ticker,
                        orderbooks,
                        candles_1m,
                        candles_5m,
                        candles_15m,
                        btc_ticker,
                        btc_candles_5m,
                        btc_candles_15m,
                    ) = await asyncio.gather(
                        client.ticker(market),
                        self._orderbook_samples(client, market),
                        client.candles(market, "minute1", 120),
                        client.candles(market, "minute5", 120),
                        client.candles(market, "minute15", 120),
                        client.ticker("KRW-BTC"),
                        client.candles("KRW-BTC", "minute5", 120),
                        client.candles("KRW-BTC", "minute15", 120),
                    )
                finally:
                    await client.close()
            if self._relative_strength_provider is not None:
                # Reconfirm leadership after the completed-candle wait. A coin
                # that lost its rank during the pullback must not pass on stale
                # raw-signal momentum.
                alert.update(
                    self._relative_strength_provider(market, int(time.time()))
                )
            candidate, rejected = evaluate_candidate(
                alert,
                ticker,
                orderbooks[-1],
                candles_1m,
                candles_5m,
                self.config,
                candles_15m=candles_15m,
                btc_ticker=btc_ticker,
                btc_candles_5m=btc_candles_5m,
                btc_candles_15m=btc_candles_15m,
                orderbook_samples=orderbooks,
            )
            if candidate is None:
                LOGGER.info(
                    "Candidate rejected for %s: %s", market, "; ".join(rejected)
                )
                self._remember_rejected(
                    alert, rejected, float(ticker["trade_price"])
                )
                self.dispatcher.record_candidate_rejection(rejected)
                MONITOR_STATE.add_screening_record(
                    self._screening_record(alert, "rejected", rejected)
                )
                return
            verification_client = UpbitPublicClient()
            try:
                latest_ticker = await verification_client.ticker(market)
            finally:
                await verification_client.close()
            candidate, dispatch_rejection = _revalidate_candidate_for_dispatch(
                candidate, float(latest_ticker["trade_price"])
            )
            if candidate is None:
                LOGGER.info(
                    "Candidate dispatch cancelled for %s: %s",
                    market,
                    dispatch_rejection,
                )
                self.dispatcher.record_candidate_rejection(
                    [str(dispatch_rejection or "전송 직전 재검증 실패")]
                )
                MONITOR_STATE.add_screening_record(
                    self._screening_record(
                        alert,
                        "dispatch_rejected",
                        [str(dispatch_rejection or "전송 직전 재검증 실패")],
                    )
                )
                return
            candidate["analysis_time_utc"] = datetime.now(timezone.utc).isoformat()
            delivered = await self.dispatcher.send_candidate(candidate)
            if not delivered:
                self._remember_rejected(
                    alert,
                    ["Telegram 일일 가용성·상한 정책으로 전송 보류"],
                    float(latest_ticker["trade_price"]),
                )
                MONITOR_STATE.add_screening_record(
                    self._screening_record(
                        alert,
                        "delivery_suppressed",
                        ["Telegram 일일 가용성·상한 정책으로 전송 보류"],
                    )
                )
                return
            accepted_record = self._screening_record(alert, "accepted", [])
            for key in (
                "score",
                "target_mode",
                "day_change_pct",
                "rsi_1m",
                "rsi_5m",
                "completed_1m_volume_ratio",
                "volume_vs_previous",
                "close_position",
                "upper_wick_ratio",
                "orderbook_bid_ask_ratio",
                "spread_pct",
                "resistance_room_pct",
                "risk_reward",
                "first_retest_confirmed",
                "early_trend",
            ):
                accepted_record[key] = candidate.get(key)
            MONITOR_STATE.add_screening_record(accepted_record)
            self._watchlist.pop(market, None)
            if market in self._signal_tracks:
                self._signal_tracks[market]["approved_candidate"] = True
            lifecycle_now = time.time()
            self._start_trend_track(candidate, lifecycle_now)
            self._lifecycles[market] = {
                "source_signal": candidate["source_signal"],
                "entry_low": candidate["entry_low"],
                "entry_high": candidate["entry_high"],
                "chase_limit": candidate["chase_limit"],
                "stop_price": candidate["stop_price"],
                "target_1": candidate["target_1"],
                "entry_price": candidate["entry_reference_price"],
                "max_price": candidate["current_price"],
                "min_price": candidate["current_price"],
                "score": candidate["score"],
                "is_reentry": candidate["is_reentry"],
                "created_at": time.time(),
                "breakout_level": candidate["breakout_level"],
                "consolidation_minutes": alert.get("consolidation_minutes"),
                "relative_strength_ready": candidate.get(
                    "relative_strength_ready"
                ),
                "relative_strength_eligible": candidate.get(
                    "relative_strength_eligible"
                ),
                "relative_strength_rank": candidate.get(
                    "relative_strength_rank"
                ),
                "relative_strength_universe": candidate.get(
                    "relative_strength_universe"
                ),
                "relative_strength_percentile": candidate.get(
                    "relative_strength_percentile"
                ),
                "momentum_5m_pct": candidate.get("momentum_5m_pct"),
                "momentum_15m_pct": candidate.get("momentum_15m_pct"),
                "momentum_60m_pct": candidate.get("momentum_60m_pct"),
                "waiting_retest": False,
                "expires_at": lifecycle_now + self.config.reentry_window_seconds,
            }
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            LOGGER.error("Candidate analysis failed for %s: %s", market, exc)


async def _resolve_markets(config: MonitorConfig) -> list[str]:
    if not config.all_krw_markets:
        return [UpbitPublicClient.normalize_market(value) for value in config.markets]
    client = UpbitPublicClient()
    try:
        rows = await client.markets()
    finally:
        await client.close()
    return sorted(
        str(row["market"])
        for row in rows
        if str(row.get("market", "")).startswith("KRW-")
    )


async def run_monitor_forever() -> None:
    config = MonitorConfig.from_env()
    dispatcher = AlertDispatcher()
    engine = SignalEngine(config)
    candidate_analyzer = CandidateAnalyzer(
        CandidateConfig.from_env(), dispatcher, engine.relative_strength_snapshot
    )
    backoff = 1
    inactivity_status_task = asyncio.create_task(
        dispatcher.run_inactivity_status_loop()
    )

    try:
        while True:
            try:
                markets = await _resolve_markets(config)
                if not markets:
                    raise RuntimeError("No KRW markets were resolved")
                MONITOR_STATE.mark_started(len(markets), dispatcher.mode)
                request = [
                    {"ticket": f"upbit-monitor-{uuid.uuid4()}"},
                    {
                        "type": "trade",
                        "codes": markets,
                        "is_only_realtime": True,
                    },
                    {"format": "DEFAULT"},
                ]
                async with websockets.connect(
                    _WS_URL,
                    ping_interval=30,
                    ping_timeout=20,
                    close_timeout=5,
                    open_timeout=15,
                    max_size=2**20,
                ) as websocket:
                    await websocket.send(json.dumps(request))
                    MONITOR_STATE.mark_connected()
                    LOGGER.info(
                        "Connected to Upbit WebSocket for %d markets", len(markets)
                    )
                    backoff = 1
                    async for raw in websocket:
                        MONITOR_STATE.mark_message()
                        if isinstance(raw, bytes):
                            raw = raw.decode("utf-8")
                        message = json.loads(raw)
                        if "error" in message:
                            raise RuntimeError(str(message["error"]))
                        if message.get("type") != "trade":
                            continue
                        market = str(message["code"])
                        price = float(message["trade_price"])
                        candidate_analyzer.observe_price(market, price)
                        alerts = engine.update(
                            market=market,
                            price=price,
                            volume=float(message["trade_volume"]),
                            timestamp_ms=int(
                                message.get("trade_timestamp")
                                or message["timestamp"]
                            ),
                        )
                        for alert in alerts:
                            if alert.get("internal_only"):
                                candidate_analyzer.watch_preleader(alert)
                                continue
                            candidate_analyzer.schedule(alert)
                            try:
                                await dispatcher.send(alert)
                            except Exception as exc:  # keep market monitoring alive
                                LOGGER.error("Alert delivery failed: %s", exc)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                MONITOR_STATE.mark_disconnected(exc)
                LOGGER.exception("Upbit monitor disconnected; retrying in %ss", backoff)
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, 60)
    finally:
        inactivity_status_task.cancel()
        await asyncio.gather(inactivity_status_task, return_exceptions=True)


_MONITOR_THREAD: threading.Thread | None = None
_MONITOR_THREAD_LOCK = threading.Lock()


def start_monitor_thread() -> bool:
    """Start one daemon monitor thread when enabled by the environment."""
    global _MONITOR_THREAD
    if not _enabled("ENABLE_MARKET_MONITOR"):
        return False
    with _MONITOR_THREAD_LOCK:
        if _MONITOR_THREAD and _MONITOR_THREAD.is_alive():
            return True
        _MONITOR_THREAD = threading.Thread(
            target=lambda: asyncio.run(run_monitor_forever()),
            name="upbit-websocket-monitor",
            daemon=True,
        )
        _MONITOR_THREAD.start()
    return True


def public_config() -> dict[str, Any]:
    """Return non-secret threshold configuration for inspection."""
    return {
        **asdict(MonitorConfig.from_env()),
        "candidate_analysis": CandidateConfig.from_env().public(),
    }
