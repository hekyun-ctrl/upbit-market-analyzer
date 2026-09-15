"""Restricted, read-only client for Upbit public quotation endpoints."""

from __future__ import annotations

import asyncio
import copy
import os
import re
import threading
import time
from typing import Any

import httpx

_BASE_URL = os.getenv("UPBIT_BASE_URL", "https://api.upbit.com").rstrip("/")
_TIMEOUT = float(os.getenv("REQUEST_TIMEOUT_SECONDS", "10"))
_ALLOWED_BASE_URLS = {"https://api.upbit.com"}
_MARKET_RE = re.compile(r"^KRW-[A-Z0-9]{1,12}$")
_ALLOWED_CANDLE_UNITS = {1, 3, 5, 10, 15, 30, 60, 240}
_REQUEST_INTERVAL_SECONDS = max(
    0.1, float(os.getenv("UPBIT_REQUEST_INTERVAL_SECONDS", "0.12"))
)
_RETRY_ATTEMPTS = max(1, min(6, int(os.getenv("UPBIT_RETRY_ATTEMPTS", "4"))))
_RATE_LOCK = threading.Lock()
_CACHE_LOCK = threading.Lock()
_NEXT_REQUEST_AT = 0.0
_CACHE: dict[tuple[str, tuple[tuple[str, str], ...]], tuple[float, Any]] = {}


async def _reserve_request_slot() -> None:
    """Pace all public REST clients together to avoid quotation API bursts."""
    global _NEXT_REQUEST_AT
    with _RATE_LOCK:
        now = time.monotonic()
        reserved_at = max(now, _NEXT_REQUEST_AT)
        _NEXT_REQUEST_AT = reserved_at + _REQUEST_INTERVAL_SECONDS
    delay = reserved_at - now
    if delay > 0:
        await asyncio.sleep(delay)


def _retry_delay(response: httpx.Response | None, attempt: int) -> float:
    if response is not None:
        raw = response.headers.get("Retry-After", "").strip()
        try:
            return max(0.2, min(10.0, float(raw)))
        except ValueError:
            pass
    return min(5.0, 0.35 * (2**attempt))


class UpbitPublicClient:
    """Calls only explicitly allowlisted quotation endpoints without credentials."""

    def __init__(self) -> None:
        if _BASE_URL not in _ALLOWED_BASE_URLS:
            raise RuntimeError("UPBIT_BASE_URL is not an allowed public Upbit host")
        self._client = httpx.AsyncClient(
            base_url=_BASE_URL,
            timeout=_TIMEOUT,
            headers={"User-Agent": "upbit-market-analyzer/1.0"},
            follow_redirects=False,
        )

    @staticmethod
    def normalize_market(market: str) -> str:
        value = market.strip().upper()
        if not _MARKET_RE.fullmatch(value):
            raise ValueError("market must have the form KRW-BTC")
        return value

    async def _get(
        self,
        path: str,
        params: dict[str, Any] | None = None,
        *,
        cache_seconds: float = 0.0,
    ) -> Any:
        normalized = tuple(
            sorted((str(key), str(value)) for key, value in (params or {}).items())
        )
        cache_key = (path, normalized)
        if cache_seconds > 0:
            with _CACHE_LOCK:
                cached = _CACHE.get(cache_key)
                if cached and cached[0] > time.monotonic():
                    return copy.deepcopy(cached[1])

        response: httpx.Response | None = None
        for attempt in range(_RETRY_ATTEMPTS):
            await _reserve_request_slot()
            try:
                response = await self._client.get(path, params=params)
            except httpx.TransportError:
                if attempt + 1 >= _RETRY_ATTEMPTS:
                    raise
                await asyncio.sleep(_retry_delay(None, attempt))
                continue
            if response.status_code == 429 or response.status_code >= 500:
                if attempt + 1 >= _RETRY_ATTEMPTS:
                    response.raise_for_status()
                await asyncio.sleep(_retry_delay(response, attempt))
                continue
            response.raise_for_status()
            data = response.json()
            if cache_seconds > 0:
                with _CACHE_LOCK:
                    if len(_CACHE) >= 1024:
                        expired = [
                            key
                            for key, (expires_at, _) in _CACHE.items()
                            if expires_at <= time.monotonic()
                        ]
                        for key in expired:
                            _CACHE.pop(key, None)
                        if len(_CACHE) >= 1024:
                            _CACHE.pop(next(iter(_CACHE)))
                    _CACHE[cache_key] = (
                        time.monotonic() + cache_seconds,
                        copy.deepcopy(data),
                    )
            return data
        raise RuntimeError("Upbit public request retry loop exhausted")

    async def markets(self) -> list[dict[str, Any]]:
        return await self._get(
            "/v1/market/all", {"isDetails": "false"}, cache_seconds=300
        )

    async def ticker(self, market: str) -> dict[str, Any]:
        data = await self._get(
            "/v1/ticker",
            {"markets": self.normalize_market(market)},
            cache_seconds=1,
        )
        if not data:
            raise ValueError("No ticker data returned")
        return data[0]

    async def orderbook(self, market: str) -> dict[str, Any]:
        data = await self._get("/v1/orderbook", {"markets": self.normalize_market(market)})
        if not data:
            raise ValueError("No orderbook data returned")
        return data[0]

    async def candles(
        self, market: str, interval: str = "day", count: int = 120
    ) -> list[dict[str, Any]]:
        market = self.normalize_market(market)
        count = max(20, min(int(count), 200))
        if interval == "day":
            path = "/v1/candles/days"
        elif interval.startswith("minute"):
            try:
                unit = int(interval.removeprefix("minute"))
            except ValueError as exc:
                raise ValueError("interval must be day or minute1/3/5/10/15/30/60/240") from exc
            if unit not in _ALLOWED_CANDLE_UNITS:
                raise ValueError("unsupported minute interval")
            path = f"/v1/candles/minutes/{unit}"
        else:
            raise ValueError("interval must be day or minute1/3/5/10/15/30/60/240")
        return await self._get(
            path, {"market": market, "count": count}, cache_seconds=12
        )

    async def close(self) -> None:
        await self._client.aclose()
