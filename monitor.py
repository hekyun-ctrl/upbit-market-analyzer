"""Always-on Upbit public trade monitor with optional Telegram alerts."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import threading
import time
import uuid
from collections import Counter, defaultdict, deque
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from statistics import mean, median
from typing import Any, Callable

import httpx
import websockets

from candidate_analysis import (
    CandidateConfig,
    evaluate_candidate,
    validate_candidate_survival,
)
from upbit_client import UpbitPublicClient

LOGGER = logging.getLogger("upbit-monitor")


class _TelegramTokenFilter(logging.Filter):
    """Remove Telegram credentials from request and exception messages."""

    _url = re.compile(r"(https://api\.telegram\.org/bot)[^/\s\"']+")

    def filter(self, record: logging.LogRecord) -> bool:
        message = record.getMessage()
        redacted = self._url.sub(r"\1[REDACTED]", message)
        if redacted != message:
            record.msg = redacted
            record.args = ()
        return True


_TELEGRAM_TOKEN_FILTER = _TelegramTokenFilter()


def _protect_telegram_logs() -> None:
    # The server may reconfigure logging after this module is imported. Apply
    # protection again immediately before every outbound Telegram request.
    for name in ("upbit-monitor", "httpx", "httpcore"):
        logger = logging.getLogger(name)
        if _TELEGRAM_TOKEN_FILTER not in logger.filters:
            logger.addFilter(_TELEGRAM_TOKEN_FILTER)
        if name != "upbit-monitor":
            logger.setLevel(logging.WARNING)


_protect_telegram_logs()
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
    extended_leader_enabled: bool
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
            extended_leader_enabled=_enabled(
                "MONITOR_EXTENDED_LEADER_ENABLED", True
            ),
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
        self.early_watch_events: deque[dict[str, Any]] = deque(maxlen=2000)
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

    def add_early_watch_event(self, event: dict[str, Any]) -> None:
        """Audit an early leader from detection through two-hour follow-through."""
        with self._lock:
            self.early_watch_events.appendleft(dict(event))
        LOGGER.info("EARLY_WATCH_AUDIT %s", json.dumps(event, ensure_ascii=False))

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

    @staticmethod
    def _early_watch_summary(events: list[dict[str, Any]]) -> dict[str, Any]:
        starts = [item for item in events if item.get("event") == "started"]
        outcomes = [item for item in events if item.get("event") == "outcome"]
        approved_ids = {
            item.get("lifecycle_id")
            for item in events
            if item.get("event") == "screening"
            and item.get("decision") == "accepted"
        }
        target_first = sum(
            item.get("first_touch_result") == "target_first" for item in outcomes
        )
        stop_first = sum(
            item.get("first_touch_result") == "stop_first" for item in outcomes
        )
        decided = target_first + stop_first
        horizon_performance: dict[str, dict[str, Any]] = {}
        for minutes in (15, 30, 60, 120):
            samples = [
                item
                for item in events
                if item.get("event") == "horizon"
                and int(item.get("horizon_minutes") or 0) == minutes
            ]
            horizon_performance[f"{minutes}m"] = {
                "sample_count": len(samples),
                "positive_count": sum(
                    float(item.get("current_return_pct") or 0) > 0
                    for item in samples
                ),
                "reached_3pct": sum(
                    float(item.get("mfe_pct") or 0) >= 3.0 for item in samples
                ),
                "reached_5pct": sum(
                    float(item.get("mfe_pct") or 0) >= 5.0 for item in samples
                ),
                "average_mfe_pct": (
                    round(
                        mean(float(item.get("mfe_pct") or 0) for item in samples),
                        2,
                    )
                    if samples
                    else None
                ),
                "average_mae_pct": (
                    round(
                        mean(float(item.get("mae_pct") or 0) for item in samples),
                        2,
                    )
                    if samples
                    else None
                ),
            }
        return {
            "started_count": len(starts),
            "completed_count": len(outcomes),
            "candidate_approved_count": len(approved_ids),
            "candidate_conversion_rate_pct": (
                round(len(approved_ids) / len(starts) * 100, 2) if starts else None
            ),
            "decided_count": decided,
            "target_first": target_first,
            "stop_first": stop_first,
            "target_first_rate_pct": (
                round(target_first / decided * 100, 2) if decided else None
            ),
            "missed_target_first": sum(
                item.get("first_touch_result") == "target_first"
                and not bool(item.get("candidate_approved"))
                for item in outcomes
            ),
            "horizons": horizon_performance,
            "recent_outcomes": outcomes[:100],
            "recent_events": events[:200],
        }

    @staticmethod
    def _on_kst_day(value: Any, day_kst: str) -> bool:
        if not value:
            return False
        try:
            observed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        except (TypeError, ValueError):
            return False
        if observed.tzinfo is None:
            observed = observed.replace(tzinfo=timezone.utc)
        return observed.astimezone(_KST).date().isoformat() == day_kst

    def daily_performance(self, day_kst: str, cost_pct: float = 0.2) -> dict[str, Any]:
        """Return a restart-scoped daily audit without assuming real fills."""
        with self._lock:
            candidate_outcomes = list(self.candidate_outcomes)
            signal_outcomes = list(self.signal_outcomes)
            early_watch_events = list(self.early_watch_events)
            screening_records = list(self.screening_records)
        outcomes = [
            item
            for item in candidate_outcomes
            if self._on_kst_day(item.get("completed_at_utc"), day_kst)
        ]
        raw = [
            item
            for item in signal_outcomes
            if self._on_kst_day(item.get("completed_at_utc"), day_kst)
        ]
        accepted = [
            item
            for item in screening_records
            if item.get("decision") == "accepted"
            and self._on_kst_day(item.get("time_utc"), day_kst)
        ]
        early_watch = self._early_watch_summary(
            [
                item
                for item in early_watch_events
                if self._on_kst_day(item.get("time_utc"), day_kst)
            ]
        )
        target_count = sum(item.get("result") == "target_1_first" for item in outcomes)
        stop_count = sum(item.get("result") == "stop_first" for item in outcomes)
        expired_count = sum(item.get("result") == "expired" for item in outcomes)
        simulated_returns = []
        for item in outcomes:
            entry = float(item.get("entry_price") or 0)
            exit_price = float(item.get("exit_price") or 0)
            if entry > 0 and exit_price > 0:
                simulated_returns.append((exit_price / entry - 1) * 100 - cost_pct)
        missed_raw_winners = sum(
            item.get("result") == "target_first"
            and not bool(item.get("approved_candidate"))
            for item in raw
        )
        return {
            "day_kst": day_kst,
            "accepted_count": len(accepted),
            "unique_markets": len({item.get("market") for item in accepted}),
            "completed_count": len(outcomes),
            "target_1_first": target_count,
            "stop_first": stop_count,
            "expired": expired_count,
            "target_rate_pct": (
                round(target_count / (target_count + stop_count) * 100, 1)
                if target_count + stop_count
                else None
            ),
            "simulated_return_sum_pct": round(sum(simulated_returns), 2),
            "simulated_return_average_pct": (
                round(mean(simulated_returns), 2) if simulated_returns else None
            ),
            "assumed_cost_pct_per_candidate": round(cost_pct, 3),
            "raw_decided_count": sum(
                item.get("result") in {"target_first", "stop_first"} for item in raw
            ),
            "raw_target_first": sum(
                item.get("result") == "target_first" for item in raw
            ),
            "missed_raw_winners": missed_raw_winners,
            "early_watch_started": early_watch["started_count"],
            "early_watch_completed": early_watch["completed_count"],
            "early_watch_target_first": early_watch["target_first"],
            "early_watch_stop_first": early_watch["stop_first"],
            "early_watch_candidate_approved": early_watch[
                "candidate_approved_count"
            ],
            "early_watch_missed_target_first": early_watch[
                "missed_target_first"
            ],
            "early_watch_horizons": early_watch["horizons"],
            "restart_scoped": True,
        }

    def candidate_performance(self) -> dict[str, Any]:
        with self._lock:
            outcomes = list(self.candidate_outcomes)
            signal_outcomes = list(self.signal_outcomes)
            early_watch_events = list(self.early_watch_events)
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
            "early_watch_performance": self._early_watch_summary(
                early_watch_events
            ),
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
                "early_watch_event_count": len(self.early_watch_events),
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

    def warm_market(
        self,
        market: str,
        candles: list[dict[str, Any]],
        *,
        now: int | None = None,
    ) -> bool:
        """Seed rolling momentum and minute values after a process restart."""
        current_second = int(now or time.time())
        boundary = current_second - current_second % 60
        historical_prices: dict[int, float] = {}
        historical_buckets: dict[int, _SecondBucket] = {}
        for candle in candles:
            raw_time = candle.get("candle_date_time_utc") or candle.get("time_utc")
            if not raw_time:
                continue
            try:
                opened = datetime.fromisoformat(
                    str(raw_time).replace("Z", "+00:00")
                )
            except ValueError:
                continue
            if opened.tzinfo is None:
                opened = opened.replace(tzinfo=timezone.utc)
            opened_second = int(opened.timestamp())
            if opened_second + 60 > boundary or opened_second < current_second - 3720:
                continue
            sample_second = opened_second + 59
            close = float(candle.get("trade_price") or 0)
            if close <= 0:
                continue
            historical_prices[sample_second] = close
            historical_buckets[sample_second] = _SecondBucket(
                second=sample_second,
                opening_price=float(candle.get("opening_price") or close),
                high_price=float(candle.get("high_price") or close),
                low_price=float(candle.get("low_price") or close),
                closing_price=close,
                trade_value=float(candle.get("candle_acc_trade_price") or 0),
            )

        if len(historical_prices) < 20:
            return False
        for second, price in self._momentum_prices.get(market, ()):
            historical_prices[second] = price
        for bucket in self._windows.get(market, ()):
            historical_buckets[bucket.second] = bucket
        self._momentum_prices[market] = deque(sorted(historical_prices.items()))
        self._windows[market] = deque(
            historical_buckets[key] for key in sorted(historical_buckets)
        )
        return True

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
        positive_count = sum(1 for _, _, change, _, _ in snapshots if change > 0)
        breadth_5m_pct = positive_count / universe * 100 if universe else 0.0
        median_5m_pct = (
            float(median(item[2] for item in snapshots)) if snapshots else 0.0
        )
        market_regime = (
            "risk_on"
            if breadth_5m_pct >= 60 and median_5m_pct >= 0.15
            else "risk_off"
            if breadth_5m_pct <= 35 or median_5m_pct <= -0.35
            else "neutral"
        )
        market_context = {
            "market_breadth_5m_pct": round(breadth_5m_pct, 1),
            "market_median_5m_pct": round(median_5m_pct, 2),
            "market_regime": market_regime,
        }
        selected = next((item for item in snapshots if item[0] == market), None)
        if selected is None:
            return {
                "relative_strength_ready": ready,
                "relative_strength_universe": universe,
                **market_context,
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
            **market_context,
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
            1920
            if self.config.preleader_enabled or self.config.extended_leader_enabled
            else 0,
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

        def confirmation_started_at(price_level: float) -> str:
            first = next(
                (
                    bucket
                    for bucket in recent
                    if bucket.high_price >= price_level
                ),
                recent[-1],
            )
            return datetime.fromtimestamp(
                first.second, tz=timezone.utc
            ).isoformat()

        morning_window = self._in_preleader_window(now)
        if (
            (morning_window or self.config.extended_leader_enabled)
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
                has_morning_impulse = bool(
                    morning_window
                    and value_1m >= self.config.min_trade_value_krw
                    and ratio_10m >= self.config.preleader_min_value_ratio_10m
                    and ratio_30m >= self.config.preleader_min_value_ratio_30m
                    and change_3m >= self.config.preleader_min_3m_pct
                    and change_5m >= self.config.preleader_min_5m_pct
                )
                strong_volume_impulse = bool(
                    not morning_window
                    and self.config.extended_leader_enabled
                    and value_1m >= max(100_000_000, self.config.min_trade_value_krw)
                    and change_3m >= 1.0
                    and change_5m >= 1.5
                    and ratio_10m >= max(8.0, self.config.preleader_min_value_ratio_10m)
                    and ratio_30m >= max(8.0, self.config.preleader_min_value_ratio_30m)
                )
                if (
                    (has_morning_impulse or strong_volume_impulse)
                    and now - self._last_preleader_at.get(market, 0)
                    >= self.config.preleader_cooldown_seconds
                ):
                    # Rank the entire market only after a qualifying volume
                    # impulse; scanning it for every coin every 15 seconds all
                    # day would starve the live WebSocket consumer.
                    relative_strength = self.relative_strength_snapshot(market, now)
                    percentile = float(
                        relative_strength.get("relative_strength_percentile") or 100.0
                    )
                    rank_improvement = self._rank_improvement(market, now, percentile)
                    leadership_accelerating = bool(
                        relative_strength.get("relative_strength_ready")
                        and percentile <= self.config.preleader_max_percentile
                        and (
                            percentile <= self.config.relative_strength_top_percent
                            or rank_improvement
                            >= self.config.preleader_rank_improvement_pct
                        )
                    )
                    extended_leader = bool(
                        strong_volume_impulse
                        and relative_strength.get("relative_strength_eligible")
                        and relative_strength.get("early_trend")
                        and percentile <= 2.0
                        and (relative_strength.get("momentum_15m_pct") or 0) > 0
                    )
                    if leadership_accelerating and (morning_window or extended_leader):
                        self._last_preleader_at[market] = now
                        signals.append(
                            (
                                "leader_volume_acceleration",
                                (
                                    "09시 전후 거래대금 선행 가속"
                                    if morning_window
                                    else "장중 선도주 거래대금 급가속"
                                ),
                                {
                                    "internal_only": True,
                                    "notify_early_watch": morning_window,
                                    "confirmation_started_at_utc": confirmation_started_at(
                                        start_price
                                        * (
                                            1
                                            + self.config.preleader_min_3m_pct / 200
                                        )
                                    ),
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
                                "confirmation_started_at_utc": confirmation_started_at(
                                    range_high
                                    * (
                                        1
                                        + self.config.rebreakout_price_buffer_pct
                                        / 200
                                    )
                                ),
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
            precursor_pct = max(0.35, self.config.price_surge_1m_pct * 0.4)
            signals.append(
                (
                    "price_volume_surge",
                    "가격·거래대금 급증",
                    {
                        "confirmation_started_at_utc": confirmation_started_at(
                            start_price * (1 + precursor_pct / 100)
                        )
                    },
                )
            )

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
                        {
                            "prior_high": prior_high,
                            "breakout_level": breakout_level,
                            "confirmation_started_at_utc": confirmation_started_at(
                                prior_high
                                * (1 + self.config.breakout_pct / 200)
                            ),
                        },
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
    if candidate.get("selection_lane") == "fast_leader":
        labels.append("초고속 선도주")
    elif candidate.get("selection_lane") == "early_leader":
        labels.append("선도주 정밀형")
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
    regime_line = ""
    if candidate.get("relative_strength_ready"):
        regime_label = {
            "risk_on": "확산 상승",
            "risk_off": "확산 약세",
            "neutral": "중립",
        }.get(str(candidate.get("market_regime")), "중립")
        regime_line = (
            f"시장 환경: {regime_label} · 5분 상승 종목 "
            f"{float(candidate.get('market_breadth_5m_pct') or 0):.0f}%\n"
        )
    extension_line = ""
    if candidate.get("target_3") and candidate.get("target_4"):
        extension_line = (
            f"추세 확장 관찰: {_format_price(float(candidate['target_3']))} "
            f"(+{float(candidate['target_3_pct']):.0f}%) · "
            f"{_format_price(float(candidate['target_4']))} "
            f"(+{float(candidate['target_4_pct']):.0f}%)\n"
        )
    survival_line = ""
    if candidate.get("survival_confirmed"):
        if candidate.get("selection_lane") == "fast_leader":
            survival_line = (
                "신속 확인: "
                f"{int(candidate.get('survival_seconds') or 0)}초 "
                "가격·거래대금·호가 유지 통과\n"
            )
        else:
            survival_line = (
                "2단계 확인: "
                f"{int(candidate.get('survival_seconds') or 0)}초 "
                "가격·거래량·호가 생존 통과\n"
            )
    wb_line = ""
    if candidate.get("double_bb_enabled"):
        wb_line = f"WB 판정: {candidate.get('double_bb_status') or '확인 중'}\n"
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
        f"{regime_line}"
        f"{survival_line}"
        f"{wb_line}"
        f"가까운 저항 여유: {candidate.get('resistance_room_pct', 0):.1f}%\n"
        f"예상 손익비: {candidate.get('risk_reward', 0):.2f}\n"
        f"추천 비중: 투자 가능금액의 {candidate['suggested_position_pct']}% 이내\n"
        f"신호 유효시간: {int(candidate['valid_seconds'] / 60)}분\n"
        f"{risk_line}"
        f"선정 근거: {reasons}\n"
        "조건점수는 적중 확률이 아닙니다. 자동 주문이나 수익 보장이 아닌 "
        "공개 시세 기반 조건부 관찰 정보입니다."
    )


def _early_watch_text(alert: dict[str, Any]) -> str:
    """Clearly separate fast discovery from a verified entry candidate."""
    rank = int(alert.get("relative_strength_rank") or 0)
    universe = int(alert.get("relative_strength_universe") or 0)
    rank_line = f"상대강도: {rank}/{universe}위\n" if rank and universe else ""
    return (
        f"[초기 포착 | 진입 검증 전] {alert['market']}\n"
        f"현재가: {_format_price(float(alert['price']))}\n"
        f"3분 변화: {float(alert.get('momentum_3m_pct') or 0):+.1f}%\n"
        f"5분 변화: {float(alert.get('momentum_5m_pct') or 0):+.1f}%\n"
        f"거래대금 가속: 최근 10분 기준 "
        f"{float(alert.get('preleader_volume_ratio_10m') or 0):.1f}배 · "
        f"30분 기준 {float(alert.get('preleader_volume_ratio_30m') or 0):.1f}배\n"
        f"{rank_line}"
        "아직 매수 후보가 아닙니다. 눌림·돌파선 유지·거래량과 호가 생존을 "
        "추가 확인한 경우에만 별도의 조건부 진입 후보를 보냅니다."
    )


def _daily_performance_text(report: dict[str, Any]) -> str:
    rate = report.get("target_rate_pct")
    rate_text = f"{float(rate):.1f}%" if rate is not None else "결정 표본 없음"
    average = report.get("simulated_return_average_pct")
    average_text = f"{float(average):+.2f}%" if average is not None else "산출 불가"
    return (
        f"[일일 후보 성과 | {report['day_kst']}]\n"
        f"확인된 후보: {int(report['accepted_count'])}건 · "
        f"고유 종목 {int(report['unique_markets'])}개\n"
        f"결과: 1차 목표 {int(report['target_1_first'])} · "
        f"손절 {int(report['stop_first'])} · 만료 {int(report['expired'])}\n"
        f"목표 선도달률: {rate_text}\n"
        f"후보당 모의 평균: {average_text} "
        f"(비용 {float(report['assumed_cost_pct_per_candidate']):.2f}% 가정)\n"
        f"원시 결정 신호: {int(report['raw_decided_count'])}건 · "
        f"+5% 선도달 {int(report['raw_target_first'])}건\n"
        f"최종 후보에서 놓친 원시 +5% 신호: {int(report['missed_raw_winners'])}건\n"
        f"초기 포착: {int(report.get('early_watch_started', 0))}건 · "
        f"2시간 관찰 완료 {int(report.get('early_watch_completed', 0))}건 · "
        f"+5% 선도달 {int(report.get('early_watch_target_first', 0))}건 · "
        f"손절 선도달 {int(report.get('early_watch_stop_first', 0))}건\n"
        f"초기 포착→조건부 후보 전환: "
        f"{int(report.get('early_watch_candidate_approved', 0))}건 · "
        f"놓친 +5% 초기 포착 "
        f"{int(report.get('early_watch_missed_target_first', 0))}건\n"
        "이 성과표는 실제 계좌 수익이 아닌 공개 시세 모의 추적이며, "
        "서비스 재시작 이후 수집된 표본 기준입니다."
    )


def _management_text(update: dict[str, Any]) -> str:
    event = str(update["event"])
    labels = {
        "target_1": "1차 목표 도달",
        "target_2": "2차 목표 도달",
        "target_3": "추세 확장 3차 도달",
        "target_4": "추세 확장 4차 도달",
        "stop": "계획 손절선 도달",
        "protected": "1차 목표 후 진입가 보호 도달",
    }
    guidance = {
        "target_1": "진입했다면 일부 수익 확정과 잔여분 손절가의 진입가 상향을 검토하세요.",
        "target_2": "진입했다면 추가 수익 확정과 잔여분 추세 추적을 검토하세요.",
        "target_3": "진입했다면 수익 보호를 우선하고 잔여분만 추세 추적을 검토하세요.",
        "target_4": "추세 확장 관찰 목표에 도달했습니다. 진입했다면 수익 보호를 우선하세요.",
        "stop": "진입했다면 사전에 정한 손실 제한 원칙을 확인하세요.",
        "protected": "1차 목표 뒤 가격이 진입가로 돌아왔습니다. 잔여분 보호 기준을 확인하세요.",
    }
    entry = float(update["entry_price"])
    current = float(update["current_price"])
    return (
        f"[후보 관리 | {labels[event]}] {update['market']}\n"
        f"후보 기준가: {_format_price(entry)}\n"
        f"현재가: {_format_price(current)} "
        f"({(current / entry - 1) * 100:+.1f}%)\n"
        f"{guidance[event]}\n"
        "실제 체결 여부를 알 수 없는 공개 시세 기반 조건부 관리 정보입니다."
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
    risk_reward_reference = float(
        candidate.get("risk_reward_reference_price") or resistance
    )
    refreshed.update(
        {
            "current_price": live_price,
            "entry_reference_price": live_price,
            "target_1_pct": round((target_1 / live_price - 1) * 100, 2),
            "target_2_pct": round((target_2 / live_price - 1) * 100, 2),
            "resistance_room_pct": round((resistance / live_price - 1) * 100, 2),
            "risk_reward": round(
                (risk_reward_reference - live_price) / risk if risk > 0 else 0.0,
                2,
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
        self._send_management_alerts = _enabled(
            "TELEGRAM_SEND_MANAGEMENT_ALERTS", True
        )
        self._send_early_watch_alerts = _enabled(
            "TELEGRAM_SEND_EARLY_WATCH_ALERTS", True
        )
        self._early_watch_daily_max = max(
            0, min(20, _env_int("TELEGRAM_EARLY_WATCH_DAILY_MAX", 8))
        )
        self._send_daily_performance = _enabled(
            "TELEGRAM_SEND_DAILY_PERFORMANCE", True
        )
        self._daily_performance_minute_kst = _env_minute_of_day(
            "TELEGRAM_DAILY_PERFORMANCE_TIME_KST", "23:50"
        )
        self._simulated_round_trip_cost_pct = max(
            0.0,
            min(
                2.0,
                _env_float("CANDIDATE_SIMULATED_ROUND_TRIP_COST_PCT", 0.2),
            ),
        )
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
        self._early_watch_delivery_count = 0
        self._last_daily_performance_day: str | None = None
        self._last_availability_delivery_at = 0.0
        self._raw_signal_times: deque[float] = deque(maxlen=5000)
        self._candidate_rejections: deque[tuple[float, tuple[str, ...]]] = deque(
            maxlen=5000
        )
        self._publish_candidate_delivery_state()

    def _telegram_url(self) -> str:
        _protect_telegram_logs()
        return f"https://api.telegram.org/bot{self._telegram_token}/sendMessage"

    @staticmethod
    def _today_kst() -> str:
        return datetime.now(_KST).date().isoformat()

    def _refresh_candidate_delivery_day(self) -> None:
        day = self._today_kst()
        if day == self._candidate_delivery_day:
            return
        self._candidate_delivery_day = day
        self._candidate_delivery_count = 0
        self._early_watch_delivery_count = 0
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
        url = self._telegram_url()
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

    async def send_daily_performance_if_due(self) -> bool:
        if self.mode != "telegram" or not self._send_daily_performance:
            return False
        now_kst = datetime.now(_KST)
        day = now_kst.date().isoformat()
        minute = now_kst.hour * 60 + now_kst.minute
        if (
            minute < self._daily_performance_minute_kst
            or self._last_daily_performance_day == day
        ):
            return False
        report = MONITOR_STATE.daily_performance(
            day, self._simulated_round_trip_cost_pct
        )
        self._last_daily_performance_day = day
        url = self._telegram_url()
        try:
            async with httpx.AsyncClient(timeout=10, follow_redirects=False) as client:
                response = await client.post(
                    url,
                    json={
                        "chat_id": self._telegram_chat_id,
                        "text": _daily_performance_text(report),
                        "disable_web_page_preview": True,
                    },
                )
                response.raise_for_status()
        except Exception:
            self._last_daily_performance_day = None
            raise
        LOGGER.warning("DAILY_CANDIDATE_PERFORMANCE %s", json.dumps(report, ensure_ascii=False))
        return True

    async def run_inactivity_status_loop(self) -> None:
        while True:
            await asyncio.sleep(60)
            try:
                await self.send_inactivity_status_if_due()
                await self.send_daily_performance_if_due()
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
        url = self._telegram_url()
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

    async def send_early_watch(self, alert: dict[str, Any]) -> bool:
        """Send a capped discovery notice that cannot be mistaken for an entry."""
        if (
            self.mode != "telegram"
            or not self._send_early_watch_alerts
            or self._early_watch_daily_max <= 0
        ):
            return False
        self._refresh_candidate_delivery_day()
        if self._early_watch_delivery_count >= self._early_watch_daily_max:
            LOGGER.info(
                "EARLY_WATCH_DAILY_MAX_SUPPRESSED market=%s count=%d max=%d",
                alert.get("market"),
                self._early_watch_delivery_count,
                self._early_watch_daily_max,
            )
            return False
        self._early_watch_delivery_count += 1
        url = self._telegram_url()
        try:
            async with httpx.AsyncClient(timeout=10, follow_redirects=False) as client:
                response = await client.post(
                    url,
                    json={
                        "chat_id": self._telegram_chat_id,
                        "text": _early_watch_text(alert),
                        "disable_web_page_preview": True,
                    },
                )
                response.raise_for_status()
        except Exception:
            self._early_watch_delivery_count -= 1
            raise
        LOGGER.warning("EARLY_WATCH %s", json.dumps(alert, ensure_ascii=False))
        return True

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
        url = self._telegram_url()
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

    async def send_management_update(self, update: dict[str, Any]) -> bool:
        LOGGER.warning(
            "CANDIDATE_MANAGEMENT %s", json.dumps(update, ensure_ascii=False)
        )
        MONITOR_STATE.add_alert(update)
        if (
            self.mode != "telegram"
            or not self._send_candidate_alerts
            or not self._send_management_alerts
        ):
            return False
        url = self._telegram_url()
        async with httpx.AsyncClient(timeout=10, follow_redirects=False) as client:
            response = await client.post(
                url,
                json={
                    "chat_id": self._telegram_chat_id,
                    "text": _management_text(update),
                    "disable_web_page_preview": True,
                },
            )
            response.raise_for_status()
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
        self._last_delivered_at: dict[str, float] = {}
        self._last_leader_recheck_at: dict[str, float] = {}
        self._lifecycles: dict[str, dict[str, Any]] = {}
        self._watchlist: dict[str, dict[str, Any]] = {}
        self._signal_tracks: dict[str, dict[str, Any]] = {}
        self._early_watch_tracks: dict[str, dict[str, Any]] = {}
        self._trend_tracks: dict[str, dict[str, Any]] = {}
        self._inflight_markets: set[str] = set()
        self._inflight_alerts: dict[str, dict[str, Any]] = {}
        self._fast_inflight_markets: set[str] = set()
        self._dispatch_locks: dict[str, asyncio.Lock] = defaultdict(asyncio.Lock)
        self._tasks: set[asyncio.Task[None]] = set()
        self._semaphore = asyncio.Semaphore(2)

    @staticmethod
    def _signal_priority(alert: dict[str, Any]) -> int:
        priority = {
            "price_volume_surge": 10,
            "leader_volume_acceleration": 20,
            "breakout": 30,
            "consolidation_rebreakout": 40,
        }.get(str(alert.get("signal")), 0)
        if alert.get("watchlist_recheck"):
            priority += 5
        if alert.get("leader_pullback_recheck"):
            priority += 10
        return priority

    @staticmethod
    def _remember_best_relative_strength(alert: dict[str, Any]) -> None:
        value = alert.get("relative_strength_percentile")
        if value is None:
            return
        percentile = float(value)
        previous = alert.get("best_relative_strength_percentile")
        if previous is None or percentile < float(previous):
            alert["best_relative_strength_percentile"] = percentile
            alert["best_relative_strength_rank"] = alert.get(
                "relative_strength_rank"
            )
            alert["best_relative_strength_universe"] = alert.get(
                "relative_strength_universe"
            )

    def _active_watch(self, market: str, now: float) -> dict[str, Any] | None:
        state = self._watchlist.get(market)
        if state and now >= float(state["expires_at"]):
            self._watchlist.pop(market, None)
            return None
        return state

    def _qualifies_fast_leader(self, alert: dict[str, Any]) -> bool:
        """Limit the incomplete-candle path to exceptional live leaders."""
        percentile = float(alert.get("relative_strength_percentile") or 100.0)
        momentum_5m = float(alert.get("momentum_5m_pct") or 0.0)
        ratio_10m = float(alert.get("preleader_volume_ratio_10m") or 0.0)
        ratio_30m = float(alert.get("preleader_volume_ratio_30m") or 0.0)
        momentum_15m_value = alert.get("momentum_15m_pct")
        momentum_15m = (
            float(momentum_15m_value) if momentum_15m_value is not None else None
        )
        narrow_market_leader = bool(
            percentile <= 1.0
            and momentum_5m >= 2.0
            and ratio_10m >= 8.0
            and ratio_30m >= 8.0
            and float(alert.get("market_breadth_5m_pct") or 0.0) >= 20.0
        )
        return bool(
            self.config.fast_leader_enabled
            and alert.get("signal") == "leader_volume_acceleration"
            and alert.get("relative_strength_ready")
            and alert.get("relative_strength_eligible")
            and percentile <= self.config.fast_leader_max_percentile
            and alert.get("early_trend")
            and momentum_5m >= self.config.relative_strength_min_5m_pct
            and (momentum_15m is None or momentum_15m > 0)
            and ratio_10m >= self.config.fast_leader_min_value_ratio_10m
            and ratio_30m >= self.config.fast_leader_min_value_ratio_30m
            and (
                str(alert.get("market_regime") or "neutral") != "risk_off"
                or narrow_market_leader
            )
        )

    def _rapid_drop_allows_leader_recovery(
        self, alert: dict[str, Any], watch: dict[str, Any] | None
    ) -> bool:
        """Treat a shallow dip as a reset when leadership is still intact."""
        if watch is None:
            return False
        percentile = float(alert.get("relative_strength_percentile") or 100.0)
        first_price = float(watch.get("first_signal_price") or alert.get("price") or 0)
        current = float(alert.get("price") or 0)
        return bool(
            alert.get("relative_strength_ready")
            and alert.get("relative_strength_eligible")
            and percentile <= self.config.early_leader_max_percentile
            and alert.get("early_trend")
            and current > 0
            and first_price > 0
            and current
            >= first_price * (1 - self.config.leader_pullback_max_pct / 100)
        )

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
                "last_rejected": ["상대강도 선도주 조기탐지 후 첫 눌림 대기"],
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
        if created and alert.get("notify_early_watch", True):
            self._start_early_watch_track(alert, now)
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
        if self._qualifies_fast_leader(alert):
            fast_alert = dict(alert)
            fast_alert.pop("internal_only", None)
            fast_alert["fast_leader"] = True
            fast_alert["breakout_level"] = price
            scheduled = self.schedule(fast_alert)
            LOGGER.info(
                "FAST_LEADER_SCHEDULED market=%s scheduled=%s rank=%s/%s "
                "confirm=%ss",
                market,
                scheduled,
                alert.get("relative_strength_rank"),
                alert.get("relative_strength_universe"),
                self.config.fast_leader_confirm_seconds,
            )
        return created

    @staticmethod
    def _early_watch_event(
        state: dict[str, Any], event: str, now: float, **values: Any
    ) -> dict[str, Any]:
        return {
            "time_utc": datetime.fromtimestamp(now, tz=timezone.utc).isoformat(),
            "event": event,
            "lifecycle_id": state["lifecycle_id"],
            "market": state["market"],
            "signal_time_utc": state.get("signal_time_utc"),
            "signal_price": state["signal_price"],
            **values,
        }

    def _start_early_watch_track(
        self, alert: dict[str, Any], now: float
    ) -> None:
        market = str(alert["market"])
        price = float(alert["price"])
        lifecycle_id = f"{market}:{alert.get('time_utc') or now}"
        state = {
            "lifecycle_id": lifecycle_id,
            "market": market,
            "signal_time_utc": alert.get("time_utc"),
            "signal_price": price,
            "target_price": price * (1 + self.config.raw_signal_target_pct / 100),
            "stop_price": price * (1 - self.config.raw_signal_stop_pct / 100),
            "created_at": now,
            "max_price": price,
            "min_price": price,
            "first_touch_result": None,
            "first_touch_at": None,
            "horizons_recorded": set(),
            "candidate_approved": False,
            "screening_count": 0,
            "last_decision": "preleader_watch",
            "last_reasons": [],
        }
        self._early_watch_tracks[market] = state
        MONITOR_STATE.add_early_watch_event(
            self._early_watch_event(
                state,
                "started",
                now,
                relative_strength_rank=alert.get("relative_strength_rank"),
                relative_strength_universe=alert.get(
                    "relative_strength_universe"
                ),
                relative_strength_percentile=alert.get(
                    "relative_strength_percentile"
                ),
                momentum_3m_pct=alert.get("momentum_3m_pct"),
                momentum_5m_pct=alert.get("momentum_5m_pct"),
                volume_ratio_10m=alert.get("preleader_volume_ratio_10m"),
                volume_ratio_30m=alert.get("preleader_volume_ratio_30m"),
            )
        )

    def _record_early_watch_screening(
        self,
        alert: dict[str, Any],
        decision: str,
        reasons: list[str],
        candidate: dict[str, Any] | None = None,
    ) -> None:
        market = str(alert["market"])
        state = self._early_watch_tracks.get(market)
        if state is None:
            return
        state["screening_count"] = int(state["screening_count"]) + 1
        state["last_decision"] = decision
        state["last_reasons"] = list(reasons)
        if decision == "accepted":
            state["candidate_approved"] = True
            state["candidate_approved_at"] = time.time()
        MONITOR_STATE.add_early_watch_event(
            self._early_watch_event(
                state,
                "screening",
                time.time(),
                decision=decision,
                reasons=list(reasons),
                source_signal=alert.get("signal"),
                score=candidate.get("score") if candidate else None,
                candidate_price=(
                    candidate.get("current_price") if candidate else None
                ),
                screening_count=state["screening_count"],
            )
        )

    def _record_early_watch_signal_upgrade(
        self, market: str, previous_signal: str, signal: str, now: float
    ) -> None:
        state = self._early_watch_tracks.get(market)
        if state is None:
            return
        MONITOR_STATE.add_early_watch_event(
            self._early_watch_event(
                state,
                "signal_upgraded",
                now,
                previous_signal=previous_signal,
                source_signal=signal,
            )
        )

    def _observe_early_watch_track(
        self, market: str, price: float, now: float
    ) -> None:
        state = self._early_watch_tracks.get(market)
        if state is None:
            return
        state["max_price"] = max(float(state["max_price"]), price)
        state["min_price"] = min(float(state["min_price"]), price)
        if state["first_touch_result"] is None:
            if price >= float(state["target_price"]):
                state["first_touch_result"] = "target_first"
                state["first_touch_at"] = now
            elif price <= float(state["stop_price"]):
                state["first_touch_result"] = "stop_first"
                state["first_touch_at"] = now
        elapsed = now - float(state["created_at"])
        entry = float(state["signal_price"])
        for minutes in (15, 30, 60, 120):
            if elapsed < minutes * 60 or minutes in state["horizons_recorded"]:
                continue
            state["horizons_recorded"].add(minutes)
            MONITOR_STATE.add_early_watch_event(
                self._early_watch_event(
                    state,
                    "horizon",
                    now,
                    horizon_minutes=minutes,
                    current_price=price,
                    current_return_pct=round((price / entry - 1) * 100, 2),
                    mfe_pct=round(
                        (float(state["max_price"]) / entry - 1) * 100, 2
                    ),
                    mae_pct=round(
                        (float(state["min_price"]) / entry - 1) * 100, 2
                    ),
                    first_touch_result=state["first_touch_result"],
                    candidate_approved=bool(state["candidate_approved"]),
                )
            )
        if elapsed < 120 * 60:
            return
        MONITOR_STATE.add_early_watch_event(
            self._early_watch_event(
                state,
                "outcome",
                now,
                exit_price=price,
                current_return_pct=round((price / entry - 1) * 100, 2),
                mfe_pct=round((float(state["max_price"]) / entry - 1) * 100, 2),
                mae_pct=round((float(state["min_price"]) / entry - 1) * 100, 2),
                first_touch_result=state["first_touch_result"] or "undecided",
                first_touch_elapsed_seconds=(
                    round(
                        float(state["first_touch_at"])
                        - float(state["created_at"]),
                        1,
                    )
                    if state["first_touch_at"] is not None
                    else None
                ),
                candidate_approved=bool(state["candidate_approved"]),
                screening_count=int(state["screening_count"]),
                last_decision=state["last_decision"],
                last_reasons=list(state["last_reasons"]),
            )
        )
        self._early_watch_tracks.pop(market, None)

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
            "selection_lane": candidate.get("selection_lane", "standard"),
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
                "selection_lane": state.get("selection_lane", "standard"),
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

    def _queue_management_update(
        self,
        market: str,
        state: dict[str, Any],
        event: str,
        price: float,
    ) -> None:
        update = {
            "time_utc": datetime.now(timezone.utc).isoformat(),
            "market": market,
            "signal": "candidate_management",
            "event": event,
            "entry_price": float(state["entry_price"]),
            "current_price": price,
            "score": int(state["score"]),
            "target_mode": state["target_mode"],
        }

        async def deliver() -> None:
            try:
                await self.dispatcher.send_management_update(update)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                LOGGER.error("Candidate management delivery failed: %s", exc)

        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return
        task = loop.create_task(deliver())
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

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
            self._queue_management_update(market, state, "target_4", price)
            self._finish_trend_track(market, state, "target_4_reached", price, now)
            return
        if (
            target_3 is not None
            and price >= float(target_3)
            and not state["target_3_reached"]
        ):
            state["target_1_reached"] = True
            state["target_2_reached"] = True
            state["target_3_reached"] = True
            self._queue_management_update(market, state, "target_3", price)
        if price >= float(state["target_2"]) and not state["target_2_reached"]:
            state["target_1_reached"] = True
            state["target_2_reached"] = True
            self._queue_management_update(market, state, "target_2", price)
            if target_4 is None:
                self._finish_trend_track(
                    market, state, "target_2_reached", price, now
                )
                return
        if price >= float(state["target_1"]) and not state["target_1_reached"]:
            state["target_1_reached"] = True
            self._queue_management_update(market, state, "target_1", price)
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
            self._queue_management_update(
                market,
                state,
                "protected" if state["target_1_reached"] else "stop",
                price,
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
        since_delivery = now - self._last_delivered_at.get(market, 0)
        if since_delivery < self.config.repeat_cooldown_seconds:
            remaining = int(self.config.repeat_cooldown_seconds - since_delivery)
            reason = f"동일 종목 후보 재전송 제한({remaining}초 남음)"
            LOGGER.info("Candidate repeat suppressed for %s: %s", market, reason)
            self.dispatcher.record_candidate_rejection([reason])
            MONITOR_STATE.add_screening_record(
                self._screening_record(alert, "repeat_suppressed", [reason])
            )
            return False
        watch = self._active_watch(market, now)
        watchlist_recheck = bool(
            watch and alert.get("signal") == "consolidation_rebreakout"
        )
        scheduled = dict(alert)
        if watchlist_recheck:
            scheduled["is_reentry"] = True
            scheduled["watchlist_recheck"] = True
            scheduled["original_signal_time_utc"] = watch.get(
                "first_signal_time_utc"
            )
        self._remember_best_relative_strength(scheduled)
        if market in self._inflight_markets:
            if scheduled.get("fast_leader"):
                if market in self._fast_inflight_markets:
                    return False
                if MONITOR_STATE.has_recent_signal(market, "rapid_drop", 600):
                    if not self._rapid_drop_allows_leader_recovery(scheduled, watch):
                        LOGGER.info("Candidate skipped after recent rapid drop: %s", market)
                        self.dispatcher.record_candidate_rejection(["최근 급락 발생"])
                        return False
                # A pending completed-candle check must not consume the only
                # opportunity to verify an exceptional live leader. Keep both
                # checks; whichever passes first still faces the same dispatch
                # guards and the per-market delivery lock.
                self._fast_inflight_markets.add(market)
                task = asyncio.create_task(self._analyze(scheduled))
                self._tasks.add(task)

                def completed_fast(done: asyncio.Task[None]) -> None:
                    self._tasks.discard(done)
                    self._fast_inflight_markets.discard(market)

                task.add_done_callback(completed_fast)
                LOGGER.info("FAST_LEADER_PARALLEL_CHECK market=%s", market)
                return True
            inflight = self._inflight_alerts.get(market)
            if (
                inflight is not None
                and self._signal_priority(scheduled)
                > self._signal_priority(inflight)
            ):
                previous_signal = str(inflight.get("signal"))
                best_percentile = min(
                    float(
                        inflight.get("best_relative_strength_percentile")
                        or 100.0
                    ),
                    float(
                        scheduled.get("best_relative_strength_percentile")
                        or 100.0
                    ),
                )
                best_rank = inflight.get("best_relative_strength_rank")
                best_universe = inflight.get("best_relative_strength_universe")
                if best_percentile == float(
                    scheduled.get("best_relative_strength_percentile") or 100.0
                ):
                    best_rank = scheduled.get("best_relative_strength_rank")
                    best_universe = scheduled.get(
                        "best_relative_strength_universe"
                    )
                inflight.clear()
                inflight.update(scheduled)
                if best_percentile < 100.0:
                    inflight["best_relative_strength_percentile"] = best_percentile
                    inflight["best_relative_strength_rank"] = best_rank
                    inflight["best_relative_strength_universe"] = best_universe
                inflight["superseded_signal"] = previous_signal
                LOGGER.info(
                    "CANDIDATE_SIGNAL_UPGRADED market=%s from=%s to=%s",
                    market,
                    previous_signal,
                    inflight.get("signal"),
                )
                MONITOR_STATE.add_screening_record(
                    self._screening_record(
                        inflight,
                        "signal_upgraded",
                        [f"{previous_signal}→{inflight.get('signal')}"],
                    )
                )
                self._record_early_watch_signal_upgrade(
                    market,
                    previous_signal,
                    str(inflight.get("signal")),
                    now,
                )
                return True
            return False
        if (
            not watchlist_recheck
            and now - self._last_checked_at.get(market, 0) < self.config.cooldown_seconds
        ):
            return False
        if MONITOR_STATE.has_recent_signal(market, "rapid_drop", 600):
            if self._rapid_drop_allows_leader_recovery(scheduled, watch):
                LOGGER.info(
                    "RAPID_DROP_RESET_AS_LEADER_PULLBACK market=%s rank=%s/%s",
                    market,
                    scheduled.get("relative_strength_rank"),
                    scheduled.get("relative_strength_universe"),
                )
            else:
                LOGGER.info("Candidate skipped after recent rapid drop: %s", market)
                self.dispatcher.record_candidate_rejection(["최근 급락 발생"])
                return False

        self._start_signal_track(scheduled, now)
        self._last_checked_at[market] = now
        self._inflight_markets.add(market)
        self._inflight_alerts[market] = scheduled
        task = asyncio.create_task(self._analyze(scheduled))
        self._tasks.add(task)

        def completed(done: asyncio.Task[None]) -> None:
            self._tasks.discard(done)
            self._inflight_markets.discard(market)
            if self._inflight_alerts.get(market) is scheduled:
                self._inflight_alerts.pop(market, None)

        task.add_done_callback(completed)
        return True

    async def _send_candidate_once(
        self, candidate: dict[str, Any]
    ) -> tuple[bool, bool]:
        """Serialize same-market sends when fast and completed checks overlap."""
        market = str(candidate["market"])
        async with self._dispatch_locks[market]:
            if (
                time.time() - self._last_delivered_at.get(market, 0)
                < self.config.repeat_cooldown_seconds
            ):
                return False, True
            delivered = await self.dispatcher.send_candidate(candidate)
            if delivered:
                self._last_delivered_at[market] = time.time()
            return delivered, False

    def _observe_leader_watchlist(
        self, market: str, price: float, now: float
    ) -> bool:
        """Recheck a rejected market after a controlled leader pullback/reclaim."""
        if not self.config.leader_watch_enabled:
            return False
        if (
            now - self._last_delivered_at.get(market, 0)
            < self.config.repeat_cooldown_seconds
        ):
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

        recent_rapid_drop = MONITOR_STATE.has_recent_signal(
            market, "rapid_drop", 600
        )
        recovery_alert = {
            "price": price,
            **relative,
        }
        if recent_rapid_drop and not self._rapid_drop_allows_leader_recovery(
            recovery_alert, state
        ):
            state["leader_pullback_seen"] = False
            LOGGER.info("Leader recheck skipped after recent rapid drop: %s", market)
            return False
        if recent_rapid_drop:
            LOGGER.info(
                "RAPID_DROP_RESET_AS_LEADER_PULLBACK market=%s drawdown=%.2f%% "
                "rank=%s/%s",
                market,
                drawdown_pct,
                relative.get("relative_strength_rank"),
                relative.get("relative_strength_universe"),
            )

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
        self._observe_early_watch_track(market, price, now)
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
        if (
            now - self._last_delivered_at.get(market, 0)
            < self.config.repeat_cooldown_seconds
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
                "selection_lane": state.get("selection_lane", "standard"),
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

    async def _market_snapshot(self, market: str) -> dict[str, Any]:
        """Fetch one internally consistent public-data bundle for screening."""
        async with self._semaphore:
            client = UpbitPublicClient()
            try:
                values = await asyncio.gather(
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
        keys = (
            "ticker",
            "orderbooks",
            "candles_1m",
            "candles_5m",
            "candles_15m",
            "btc_ticker",
            "btc_candles_5m",
            "btc_candles_15m",
        )
        return dict(zip(keys, values))

    def _evaluate_snapshot(
        self, alert: dict[str, Any], snapshot: dict[str, Any]
    ) -> tuple[dict[str, Any] | None, list[str]]:
        return evaluate_candidate(
            alert,
            snapshot["ticker"],
            snapshot["orderbooks"][-1],
            snapshot["candles_1m"],
            snapshot["candles_5m"],
            self.config,
            candles_15m=snapshot["candles_15m"],
            btc_ticker=snapshot["btc_ticker"],
            btc_candles_5m=snapshot["btc_candles_5m"],
            btc_candles_15m=snapshot["btc_candles_15m"],
            orderbook_samples=snapshot["orderbooks"],
        )

    async def _analyze(self, alert: dict[str, Any]) -> None:
        market = str(alert["market"])
        try:
            now_epoch = time.time()
            fast_leader = bool(alert.get("fast_leader"))
            if fast_leader:
                # Three orderbook samples add roughly four seconds, so a six-
                # second live hold produces a decision about ten seconds after
                # discovery without waiting for the minute boundary.
                await asyncio.sleep(self.config.fast_leader_confirm_seconds)
            else:
                seconds_to_next_close = 62 - (now_epoch % 60)
                # If the signal arrived in the final 20 seconds of a candle,
                # wait for the following close unless the move itself had
                # already been developing for at least 20 seconds.
                confirmation_started = alert.get("confirmation_started_at_utc")
                try:
                    confirmation_started_epoch = datetime.fromisoformat(
                        str(confirmation_started).replace("Z", "+00:00")
                    ).timestamp()
                except (TypeError, ValueError):
                    confirmation_started_epoch = now_epoch
                candle_close = now_epoch - (now_epoch % 60) + 60
                if candle_close - confirmation_started_epoch < 20:
                    seconds_to_next_close += 60
                await asyncio.sleep(
                    max(self.config.confirm_seconds, seconds_to_next_close)
                )
                # A stronger signal can upgrade this alert while it sleeps.
                # Recalculate using the upgraded signal and the actual minute
                # boundary; the two-second API buffer is not candle exposure.
                confirmation_started = alert.get("confirmation_started_at_utc")
                try:
                    confirmation_started_epoch = datetime.fromisoformat(
                        str(confirmation_started).replace("Z", "+00:00")
                    ).timestamp()
                except (TypeError, ValueError):
                    confirmation_started_epoch = now_epoch
                checked_at = time.time()
                last_candle_close = checked_at - (checked_at % 60)
                if last_candle_close - confirmation_started_epoch < 20:
                    await asyncio.sleep(62 - (checked_at % 60))
            snapshot = await self._market_snapshot(market)
            if self._relative_strength_provider is not None:
                # Reconfirm leadership after the completed-candle wait. A coin
                # that lost its rank during the pullback must not pass on stale
                # raw-signal momentum.
                relative_snapshot = self._relative_strength_provider(
                    market, int(time.time())
                )
                self._remember_best_relative_strength(alert)
                alert.update(relative_snapshot)
                self._remember_best_relative_strength(alert)
            candidate, rejected = self._evaluate_snapshot(alert, snapshot)
            if candidate is None:
                LOGGER.info(
                    "Candidate rejected for %s: %s", market, "; ".join(rejected)
                )
                self._remember_rejected(
                    alert, rejected, float(snapshot["ticker"]["trade_price"])
                )
                self.dispatcher.record_candidate_rejection(rejected)
                MONITOR_STATE.add_screening_record(
                    self._screening_record(alert, "rejected", rejected)
                )
                self._record_early_watch_screening(
                    alert, "rejected", rejected
                )
                return
            if fast_leader:
                candidate["survival_confirmed"] = True
                candidate["survival_seconds"] = max(
                    self.config.fast_leader_confirm_seconds,
                    int(round(time.time() - now_epoch)),
                )
                LOGGER.info(
                    "FAST_LEADER_VERIFIED market=%s seconds=%d score=%d",
                    market,
                    int(candidate["survival_seconds"]),
                    int(candidate["score"]),
                )
            else:
                first_candidate = candidate
                LOGGER.info(
                    "CANDIDATE_SURVIVAL_PENDING market=%s seconds=%d score=%d",
                    market,
                    self.config.survival_confirm_seconds,
                    int(candidate["score"]),
                )
                await asyncio.sleep(self.config.survival_confirm_seconds)
                survival_snapshot = await self._market_snapshot(market)
                live_price = float(survival_snapshot["ticker"]["trade_price"])
                candidate, original_range_rejection = (
                    _revalidate_candidate_for_dispatch(first_candidate, live_price)
                )
                survival_metrics, survival_rejected = validate_candidate_survival(
                    first_candidate,
                    survival_snapshot["ticker"],
                    survival_snapshot["orderbooks"],
                    survival_snapshot["candles_1m"],
                    self.config,
                )
                if original_range_rejection:
                    survival_rejected.insert(0, original_range_rejection)
                if candidate is None or survival_rejected:
                    reasons = [
                        f"생존 재검증: {reason}" for reason in survival_rejected
                    ]
                    LOGGER.info(
                        "Candidate survival rejected for %s: %s",
                        market,
                        "; ".join(reasons),
                    )
                    self._remember_rejected(
                        alert,
                        reasons,
                        float(survival_snapshot["ticker"]["trade_price"]),
                    )
                    self.dispatcher.record_candidate_rejection(reasons)
                    MONITOR_STATE.add_screening_record(
                        self._screening_record(alert, "survival_rejected", reasons)
                    )
                    self._record_early_watch_screening(
                        alert, "survival_rejected", reasons
                    )
                    return
                candidate.update(survival_metrics)
                candidate["survival_confirmed"] = True
                candidate["survival_seconds"] = self.config.survival_confirm_seconds
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
                self._record_early_watch_screening(
                    alert,
                    "dispatch_rejected",
                    [str(dispatch_rejection or "전송 직전 재검증 실패")],
                )
                return
            candidate["analysis_time_utc"] = datetime.now(timezone.utc).isoformat()
            delivered, repeated = await self._send_candidate_once(candidate)
            if repeated:
                LOGGER.info("Candidate repeat suppressed after validation for %s", market)
                MONITOR_STATE.add_screening_record(
                    self._screening_record(
                        alert, "repeat_suppressed", ["동일 종목 후보 이미 전송됨"]
                    )
                )
                return
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
                self._record_early_watch_screening(
                    alert,
                    "delivery_suppressed",
                    ["Telegram 일일 가용성·상한 정책으로 전송 보류"],
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
                "early_leader_lane",
                "selection_lane",
                "leader_resistance_override",
                "double_bb_status",
                "double_bb_confirmed",
                "double_bb_true_breakout",
                "double_bb_first_retest",
                "double_bb_fake_breakout",
            ):
                accepted_record[key] = candidate.get(key)
            MONITOR_STATE.add_screening_record(accepted_record)
            self._record_early_watch_screening(
                alert, "accepted", [], candidate
            )
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
                "selection_lane": candidate.get("selection_lane", "standard"),
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
                "expires_at": lifecycle_now + self.config.outcome_tracking_seconds,
            }
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            LOGGER.error("Candidate analysis failed for %s: %s", market, exc)
            self._record_early_watch_screening(
                alert, "analysis_failed", [f"{type(exc).__name__}: {exc}"]
            )


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


async def _warm_signal_engine(
    engine: SignalEngine, markets: list[str]
) -> tuple[int, int]:
    """Backfill one hour without delaying the live WebSocket connection."""
    semaphore = asyncio.Semaphore(4)
    client = UpbitPublicClient()

    async def warm_one(market: str) -> bool:
        try:
            async with semaphore:
                candles = await client.candles(market, "minute1", 65)
            return engine.warm_market(market, candles)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            LOGGER.warning("Warm start failed for %s: %s", market, exc)
            return False

    try:
        results = await asyncio.gather(*(warm_one(market) for market in markets))
    finally:
        await client.close()
    warmed = sum(results)
    LOGGER.info(
        "Signal engine warm start completed: %d/%d markets", warmed, len(markets)
    )
    return warmed, len(markets)


async def run_monitor_forever() -> None:
    config = MonitorConfig.from_env()
    dispatcher = AlertDispatcher()
    engine = SignalEngine(config)
    candidate_analyzer = CandidateAnalyzer(
        CandidateConfig.from_env(), dispatcher, engine.relative_strength_snapshot
    )
    backoff = 1
    warmup_task: asyncio.Task[tuple[int, int]] | None = None
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
                if warmup_task is None:
                    warmup_task = asyncio.create_task(
                        _warm_signal_engine(engine, markets)
                    )
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
                                created = candidate_analyzer.watch_preleader(alert)
                                if created and alert.get("notify_early_watch", True):
                                    try:
                                        await dispatcher.send_early_watch(alert)
                                    except Exception as exc:
                                        LOGGER.error("Early-watch delivery failed: %s", exc)
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
        tasks: list[asyncio.Task[Any]] = [inactivity_status_task]
        if warmup_task is not None:
            warmup_task.cancel()
            tasks.append(warmup_task)
        await asyncio.gather(*tasks, return_exceptions=True)


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
