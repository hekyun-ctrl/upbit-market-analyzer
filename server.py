"""MCP tools for read-only Upbit public market analysis."""

from __future__ import annotations

from typing import Any

from mcp.server import MCPServer

from analysis import analyze_candles
from upbit_client import UpbitPublicClient

mcp = MCPServer(
    "Upbit Market Analyzer",
    instructions=(
        "Use only public Upbit market data. Never claim to access balances, orders, "
        "deposits, withdrawals, or private API keys. Present analysis as descriptive, "
        "not guaranteed investment advice."
    ),
)


def _client() -> UpbitPublicClient:
    return UpbitPublicClient()


@mcp.tool()
async def list_krw_markets() -> list[dict[str, Any]]:
    """List publicly available KRW trading markets."""
    client = _client()
    try:
        markets = await client.markets()
        return [
            {
                "market": row["market"],
                "korean_name": row.get("korean_name"),
                "english_name": row.get("english_name"),
            }
            for row in markets
            if str(row.get("market", "")).startswith("KRW-")
        ]
    finally:
        await client.close()


@mcp.tool()
async def get_ticker(market: str) -> dict[str, Any]:
    """Get a public ticker snapshot, for example market='KRW-BTC'."""
    client = _client()
    try:
        row = await client.ticker(market)
        return {
            "market": row.get("market"),
            "trade_price": row.get("trade_price"),
            "signed_change_rate_pct": round(float(row.get("signed_change_rate", 0)) * 100, 3),
            "acc_trade_price_24h": row.get("acc_trade_price_24h"),
            "high_price": row.get("high_price"),
            "low_price": row.get("low_price"),
            "timestamp": row.get("timestamp"),
        }
    finally:
        await client.close()


@mcp.tool()
async def get_orderbook(market: str) -> dict[str, Any]:
    """Get the public orderbook snapshot for a KRW market."""
    client = _client()
    try:
        row = await client.orderbook(market)
        units = row.get("orderbook_units", [])
        return {
            "market": row.get("market"),
            "timestamp": row.get("timestamp"),
            "total_ask_size": row.get("total_ask_size"),
            "total_bid_size": row.get("total_bid_size"),
            "levels": units[:15],
        }
    finally:
        await client.close()


@mcp.tool()
async def get_candles(
    market: str, interval: str = "day", count: int = 120
) -> list[dict[str, Any]]:
    """Get public candles. interval: day or minute1/3/5/10/15/30/60/240."""
    client = _client()
    try:
        candles = await client.candles(market, interval, count)
        return [
            {
                "time_kst": c.get("candle_date_time_kst"),
                "opening_price": c.get("opening_price"),
                "high_price": c.get("high_price"),
                "low_price": c.get("low_price"),
                "trade_price": c.get("trade_price"),
                "volume": c.get("candle_acc_trade_volume"),
                "trade_value": c.get("candle_acc_trade_price"),
            }
            for c in candles
        ]
    finally:
        await client.close()


@mcp.tool()
async def analyze_market(
    market: str, interval: str = "day", count: int = 120
) -> dict[str, Any]:
    """Summarize trend, RSI, volatility and volume from public candles."""
    client = _client()
    try:
        candles = await client.candles(market, interval, max(count, 60))
        return {
            "market": client.normalize_market(market),
            "interval": interval,
            **analyze_candles(candles),
        }
    finally:
        await client.close()
