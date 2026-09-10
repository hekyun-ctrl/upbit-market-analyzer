"""Restricted, read-only client for Upbit public quotation endpoints."""

from __future__ import annotations

import os
import re
from typing import Any

import httpx

_BASE_URL = os.getenv("UPBIT_BASE_URL", "https://api.upbit.com").rstrip("/")
_TIMEOUT = float(os.getenv("REQUEST_TIMEOUT_SECONDS", "10"))
_ALLOWED_BASE_URLS = {"https://api.upbit.com"}
_MARKET_RE = re.compile(r"^KRW-[A-Z0-9]{2,12}$")
_ALLOWED_CANDLE_UNITS = {1, 3, 5, 10, 15, 30, 60, 240}


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

    async def _get(self, path: str, params: dict[str, Any] | None = None) -> Any:
        response = await self._client.get(path, params=params)
        response.raise_for_status()
        return response.json()

    async def markets(self) -> list[dict[str, Any]]:
        return await self._get("/v1/market/all", {"isDetails": "false"})

    async def ticker(self, market: str) -> dict[str, Any]:
        data = await self._get("/v1/ticker", {"markets": self.normalize_market(market)})
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
        return await self._get(path, {"market": market, "count": count})

    async def close(self) -> None:
        await self._client.aclose()
