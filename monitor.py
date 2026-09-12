"""Always-on Upbit public trade monitor with optional Telegram alerts."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import threading
import time
import uuid
from collections import defaultdict, deque
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from typing import Any

import httpx
import websockets

from candidate_analysis import CandidateConfig, evaluate_candidate
from upbit_client import UpbitPublicClient


LOGGER = logging.getLogger("upbit-monitor")
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

    @classmethod
    def from_env(cls) -> "MonitorConfig":
        raw_markets = os.getenv("MONITOR_MARKETS", "ALL_KRW")
        values = tuple(
            value.strip().upper()
            for value in raw_markets.split(",")
            if value.strip()
        )
        all_markets = not values or "ALL_KRW" in values
        selected = tuple(value for value in values if value != "ALL_KRW")
        return cls(
            markets=selected,
            all_krw_markets=all_markets,
            price_surge_1m_pct=_env_float("MONITOR_PRICE_SURGE_1M_PCT", 1.5),
            breakout_pct=_env_float("MONITOR_BREAKOUT_PCT", 0.4),
            volume_ratio=_env_float("MONITOR_VOLUME_RATIO", 2.0),
            min_trade_value_krw=_env_float(
                "MONITOR_MIN_TRADE_VALUE_KRW", 50_000_000
            ),
            alert_cooldown_seconds=max(
                60, _env_int("MONITOR_ALERT_COOLDOWN_SECONDS", 600)
            ),
            max_alerts_per_minute=max(
                1, _env_int("MONITOR_MAX_ALERTS_PER_MINUTE", 5)
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

        while window and window[0].second < second - 360:
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

        signals: list[tuple[str, str]] = []
        if (
            change_1m_pct >= self.config.price_surge_1m_pct
            and value_1m >= self.config.min_trade_value_krw
            and volume_ratio >= self.config.volume_ratio
        ):
            signals.append(("price_volume_surge", "가격·거래대금 급증"))

        if (
            change_1m_pct <= -self.config.price_surge_1m_pct
            and value_1m >= self.config.min_trade_value_krw
            and volume_ratio >= self.config.volume_ratio
        ):
            signals.append(("rapid_drop", "단기 급락 위험"))

        prior = [
            bucket
            for bucket in window
            if now - 300 <= bucket.second < now - 10
        ]
        if prior and now - window[0].second >= 180:
            prior_high = max(bucket.high_price for bucket in prior)
            breakout_level = prior_high * (1 + self.config.breakout_pct / 100)
            if (
                current_price >= breakout_level
                and change_1m_pct > 0
                and value_1m >= self.config.min_trade_value_krw
                and volume_ratio >= max(1.5, self.config.volume_ratio * 0.75)
            ):
                signals.append(("breakout", "5분 고점 돌파"))

        alerts = []
        for signal_type, label in signals:
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
    return (
        f"[업비트 실시간 감시] {alert['market']} {alert['label']}\n"
        f"현재가: {_format_price(float(alert['price']))}\n"
        f"1분 등락: {alert['change_1m_pct']:+.2f}%\n"
        f"1분 거래대금: {alert['trade_value_1m_krw']:,.0f}원\n"
        f"직전 1분 대비 거래대금: {alert['volume_ratio_vs_previous_1m']:.2f}배\n"
        "자동 주문 신호가 아니라 관찰 알림입니다. 호가와 지지·저항을 확인하세요."
    )


def _candidate_text(candidate: dict[str, Any]) -> str:
    reasons = "·".join(candidate.get("reasons", [])) or "공개 시세 조건 충족"
    return (
        f"[조건부 진입 후보 | 점수 {candidate['score']}/100] "
        f"{candidate['market']}\n"
        f"현재가: {_format_price(float(candidate['current_price']))}\n"
        f"진입구간: {_format_price(float(candidate['entry_low']))} ~ "
        f"{_format_price(float(candidate['entry_high']))}\n"
        f"추격금지: {_format_price(float(candidate['chase_limit']))} 이상\n"
        f"손절가: {_format_price(float(candidate['stop_price']))}\n"
        f"1차 목표: {_format_price(float(candidate['target_1']))}\n"
        f"2차 목표: {_format_price(float(candidate['target_2']))}\n"
        f"추천 비중: 투자 가능금액의 {candidate['suggested_position_pct']}% 이내\n"
        f"신호 유효시간: {int(candidate['valid_seconds'] / 60)}분\n"
        f"선정 근거: {reasons}\n"
        "자동 주문이나 수익 보장이 아닌 공개 시세 기반 조건부 관찰 정보입니다."
    )


class AlertDispatcher:
    def __init__(self) -> None:
        self._telegram_token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
        self._telegram_chat_id = os.getenv("TELEGRAM_CHAT_ID", "").strip()

    @property
    def mode(self) -> str:
        if self._telegram_token and self._telegram_chat_id:
            return "telegram"
        return "log_only"

    async def send(self, alert: dict[str, Any]) -> None:
        MONITOR_STATE.add_alert(alert)
        LOGGER.warning("MARKET_ALERT %s", json.dumps(alert, ensure_ascii=False))
        if self.mode != "telegram":
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
        LOGGER.warning(
            "ENTRY_CANDIDATE %s", json.dumps(candidate, ensure_ascii=False)
        )
        if self.mode != "telegram":
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


class CandidateAnalyzer:
    """Confirm positive WebSocket signals with fresh REST market snapshots."""

    def __init__(self, config: CandidateConfig, dispatcher: AlertDispatcher) -> None:
        self.config = config
        self.dispatcher = dispatcher
        self._last_checked_at: dict[str, float] = {}
        self._inflight_markets: set[str] = set()
        self._tasks: set[asyncio.Task[None]] = set()
        self._semaphore = asyncio.Semaphore(2)

    def schedule(self, alert: dict[str, Any]) -> bool:
        if not self.config.enabled:
            return False
        if alert.get("signal") not in {"price_volume_surge", "breakout"}:
            return False
        market = str(alert["market"])
        now = time.time()
        if market in self._inflight_markets:
            return False
        if now - self._last_checked_at.get(market, 0) < self.config.cooldown_seconds:
            return False
        if MONITOR_STATE.has_recent_signal(market, "rapid_drop", 600):
            LOGGER.info("Candidate skipped after recent rapid drop: %s", market)
            return False

        self._last_checked_at[market] = now
        self._inflight_markets.add(market)
        task = asyncio.create_task(self._analyze(alert))
        self._tasks.add(task)

        def completed(done: asyncio.Task[None]) -> None:
            self._tasks.discard(done)
            self._inflight_markets.discard(market)

        task.add_done_callback(completed)
        return True

    async def _analyze(self, alert: dict[str, Any]) -> None:
        market = str(alert["market"])
        try:
            await asyncio.sleep(self.config.confirm_seconds)
            async with self._semaphore:
                client = UpbitPublicClient()
                try:
                    ticker, orderbook, candles_1m, candles_5m = await asyncio.gather(
                        client.ticker(market),
                        client.orderbook(market),
                        client.candles(market, "minute1", 120),
                        client.candles(market, "minute5", 120),
                    )
                finally:
                    await client.close()
            candidate, rejected = evaluate_candidate(
                alert,
                ticker,
                orderbook,
                candles_1m,
                candles_5m,
                self.config,
            )
            if candidate is None:
                LOGGER.info(
                    "Candidate rejected for %s: %s", market, "; ".join(rejected)
                )
                return
            candidate["analysis_time_utc"] = datetime.now(timezone.utc).isoformat()
            await self.dispatcher.send_candidate(candidate)
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
                LOGGER.info("Connected to Upbit WebSocket for %d markets", len(markets))
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
                    alerts = engine.update(
                        market=str(message["code"]),
                        price=float(message["trade_price"]),
                        volume=float(message["trade_volume"]),
                        timestamp_ms=int(
                            message.get("trade_timestamp") or message["timestamp"]
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
