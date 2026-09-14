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
from datetime import datetime, timezone
from typing import Any

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

    def update(
        self, market: str, price: float, volume: float, timestamp_ms: int
    ) -> list[dict[str, Any]]:
        if price <= 0 or volume < 0:
            return []
        second = int(timestamp_ms / 1000)
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

        history_seconds = max(360, self.config.rebreakout_consolidation_seconds + 120)
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

        alerts = []
        for signal_type, label, details in signals:
            if not self._can_alert(market, signal_type, now):
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
    reentry = " | 재지지" if candidate.get("is_reentry") else ""
    return (
        f"[조건부 진입 후보{reentry} | 조건점수 {candidate['score']}/100] "
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
        f"목표 방식: {candidate['target_mode']}\n"
        f"가까운 저항 여유: {candidate.get('resistance_room_pct', 0):.1f}%\n"
        f"예상 손익비: {candidate.get('risk_reward', 0):.2f}\n"
        f"추천 비중: 투자 가능금액의 {candidate['suggested_position_pct']}% 이내\n"
        f"신호 유효시간: {int(candidate['valid_seconds'] / 60)}분\n"
        f"{risk_line}"
        f"선정 근거: {reasons}\n"
        "자동 주문이나 수익 보장이 아닌 공개 시세 기반 조건부 관찰 정보입니다."
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
            0, min(100, _env_int("TELEGRAM_CANDIDATE_MIN_SCORE", 0))
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
        self._raw_signal_times: deque[float] = deque(maxlen=5000)
        self._candidate_rejections: deque[tuple[float, tuple[str, ...]]] = deque(
            maxlen=5000
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

    async def send_candidate(self, candidate: dict[str, Any]) -> None:
        MONITOR_STATE.add_alert(candidate)
        LOGGER.warning("ENTRY_CANDIDATE %s", json.dumps(candidate, ensure_ascii=False))
        if self.mode != "telegram" or not self._send_candidate_alerts:
            return
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
            return
        url = f"https://api.telegram.org/bot{self._telegram_token}/sendMessage"
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
        self._last_candidate_delivery_at = time.monotonic()
        self._last_inactivity_status_at = self._last_candidate_delivery_at


class CandidateAnalyzer:
    """Confirm positive WebSocket signals with fresh REST market snapshots."""

    def __init__(self, config: CandidateConfig, dispatcher: AlertDispatcher) -> None:
        self.config = config
        self.dispatcher = dispatcher
        self._last_checked_at: dict[str, float] = {}
        self._last_reentry_at: dict[str, float] = {}
        self._lifecycles: dict[str, dict[str, Any]] = {}
        self._watchlist: dict[str, dict[str, Any]] = {}
        self._signal_tracks: dict[str, dict[str, Any]] = {}
        self._inflight_markets: set[str] = set()
        self._tasks: set[asyncio.Task[None]] = set()
        self._semaphore = asyncio.Semaphore(2)

    def _active_watch(self, market: str, now: float) -> dict[str, Any] | None:
        state = self._watchlist.get(market)
        if state and now >= float(state["expires_at"]):
            self._watchlist.pop(market, None)
            return None
        return state

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
            }
        )
        self._signal_tracks.pop(market, None)

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

    def observe_price(self, market: str, price: float) -> bool:
        """Schedule one fresh recheck after price leaves and retakes the entry zone."""
        now = time.time()
        self._observe_signal_track(market, price, now)
        self._active_watch(market, now)
        state = self._lifecycles.get(market)
        if not state:
            return False
        state["max_price"] = max(float(state["max_price"]), price)
        state["min_price"] = min(float(state["min_price"]), price)
        if price >= float(state["target_1"]):
            self._finish_lifecycle(market, state, "target_1_first", price, now)
            return False
        if price <= float(state["stop_price"]):
            self._finish_lifecycle(market, state, "stop_first", price, now)
            return False
        if now >= float(state["expires_at"]):
            self._finish_lifecycle(market, state, "expired", price, now)
            return False
        entry_low = float(state["entry_low"])
        entry_high = float(state["entry_high"])
        if price < entry_low or price > entry_high:
            state["waiting_retest"] = True
            return False
        if not state.get("waiting_retest") or not entry_low <= price <= entry_high:
            return False
        if market in self._inflight_markets:
            return False
        if (
            now - self._last_reentry_at.get(market, 0)
            < self.config.reentry_cooldown_seconds
        ):
            return False
        self._last_reentry_at[market] = now
        state["waiting_retest"] = False
        alert = {
            "time_utc": datetime.now(timezone.utc).isoformat(),
            "market": market,
            "signal": state["source_signal"],
            "price": price,
            "breakout_level": state["breakout_level"],
            "is_reentry": True,
        }
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
                return
            candidate["analysis_time_utc"] = datetime.now(timezone.utc).isoformat()
            await self.dispatcher.send_candidate(candidate)
            self._watchlist.pop(market, None)
            if market in self._signal_tracks:
                self._signal_tracks[market]["approved_candidate"] = True
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
                "waiting_retest": False,
                "expires_at": time.time() + self.config.reentry_window_seconds,
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
    candidate_analyzer = CandidateAnalyzer(CandidateConfig.from_env(), dispatcher)
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
