import asyncio

import httpx
import pytest

import upbit_client
from upbit_client import UpbitPublicClient


def test_one_character_krw_market_is_valid():
    assert UpbitPublicClient.normalize_market("krw-t") == "KRW-T"


@pytest.mark.parametrize("market", ["BTC-T", "KRW-", "KRW-A_B", "KRW-TOO-LONG"])
def test_non_krw_or_malformed_market_is_rejected(market):
    with pytest.raises(ValueError):
        UpbitPublicClient.normalize_market(market)


def test_public_request_retries_after_429(monkeypatch):
    request = httpx.Request("GET", "https://api.upbit.com/v1/ticker")
    responses = [
        httpx.Response(429, request=request, headers={"Retry-After": "0"}),
        httpx.Response(200, request=request, json=[{"trade_price": 100.0}]),
    ]

    class FakeClient:
        async def get(self, path, params=None):
            return responses.pop(0)

    async def no_wait():
        return None

    async def no_sleep(_seconds):
        return None

    monkeypatch.setattr(upbit_client, "_reserve_request_slot", no_wait)
    monkeypatch.setattr(upbit_client.asyncio, "sleep", no_sleep)
    client = object.__new__(UpbitPublicClient)
    client._client = FakeClient()

    result = asyncio.run(client._get("/v1/ticker", {"markets": "KRW-BTC"}))

    assert result == [{"trade_price": 100.0}]
    assert responses == []
