"""LBank BTCUSDT perpetual read-only monitor (no account keys or orders).

Dependencies: pip install ccxt
Environment: TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID, LBANK_POLL_SECONDS (default 60).
Run as a separate Railway service; do NOT replace the Upbit service.
"""
import json
import logging
import os
import time
import urllib.parse
import urllib.request
from datetime import datetime, timezone

import ccxt

LOG = logging.getLogger("lbank_monitor")
SYMBOL = "BTC/USDT:USDT"
POLL = max(30, int(os.getenv("LBANK_POLL_SECONDS", "60")))
COOLDOWN = max(300, int(os.getenv("LBANK_ALERT_COOLDOWN_SECONDS", "900")))
MAX_STALENESS_MS = 10 * 60 * 1000


def ema(values, period):
    alpha = 2.0 / (period + 1)
    result = values[0]
    out = []
    for value in values:
        result = alpha * value + (1 - alpha) * result
        out.append(result)
    return out


def rsi(values, period=14):
    changes = [values[i] - values[i - 1] for i in range(1, len(values))]
    gains = [max(v, 0) for v in changes]
    losses = [max(-v, 0) for v in changes]
    avg_gain = sum(gains[:period]) / period
    avg_loss = sum(losses[:period]) / period
    for gain, loss in zip(gains[period:], losses[period:]):
        avg_gain = (avg_gain * (period - 1) + gain) / period
        avg_loss = (avg_loss * (period - 1) + loss) / period
    return 100 if avg_loss == 0 else 100 - 100 / (1 + avg_gain / avg_loss)


def indicators(bars):
    # Caller excludes the current unfinished 5-minute candle.
    closes = [float(c[4]) for c in bars]
    volumes = [float(c[5]) for c in bars]
    if len(closes) < 40:
        raise ValueError("Not enough completed candles")
    macd_line = [a - b for a, b in zip(ema(closes, 12), ema(closes, 26))]
    signal_line = ema(macd_line, 9)
    mean20 = sum(closes[-20:]) / 20
    variance = sum((v - mean20) ** 2 for v in closes[-20:]) / 20
    return {
        "price": closes[-1],
        "rsi": rsi(closes),
        "macd": macd_line[-1],
        "signal": signal_line[-1],
        "previous_macd": macd_line[-2],
        "previous_signal": signal_line[-2],
        "middle": mean20,
        "upper": mean20 + 2 * variance ** .5,
        "lower": mean20 - 2 * variance ** .5,
        "volume_ratio": volumes[-1] / max(1e-9, sum(volumes[-21:-1]) / 20),
        "previous_high": max(float(c[2]) for c in bars[-7:-1]),
        "previous_low": min(float(c[3]) for c in bars[-7:-1]),
        "closed_at": int(bars[-1][0]) + 300_000,
    }


def signal(ind):
    # Both momentum and price-structure confirmations are required.
    bullish = (ind["price"] > ind["previous_high"] and
               ind["macd"] > ind["signal"] and ind["rsi"] >= 55 and
               ind["volume_ratio"] >= 1.5)
    bearish = (ind["price"] < ind["previous_low"] and
               ind["macd"] < ind["signal"] and ind["rsi"] <= 45 and
               ind["volume_ratio"] >= 1.5)
    if bullish and not bearish:
        return "LONG_WATCH"
    if bearish and not bullish:
        return "SHORT_WATCH"
    return None


def telegram(message):
    token = os.getenv("TELEGRAM_BOT_TOKEN", "")
    chat = os.getenv("TELEGRAM_CHAT_ID", "")
    if not token or not chat:
        LOG.info("Telegram not configured; alert (local only): %s", message)
        return False
    payload = urllib.parse.urlencode({"chat_id": chat, "text": message}).encode()
    req = urllib.request.Request(
        "https://api.telegram.org/bot" + token + "/sendMessage",
        data=payload, method="POST")
    with urllib.request.urlopen(req, timeout=10) as response:
        result = json.load(response)
    return bool(result.get("ok"))


def run():
    exchange = ccxt.lbank({"enableRateLimit": True, "options": {"defaultType": "swap"}})
    exchange.load_markets()
    market = exchange.market(SYMBOL)
    if not market.get("swap"):
        raise RuntimeError("Contract market unavailable; refuse to use spot data")
    last_bar = None
    last_alert = {}
    LOG.info("LBank read-only BTC perpetual monitor started")
    while True:
        try:
            rows = exchange.fetch_ohlcv(SYMBOL, "5m", limit=120)
            now_ms = int(time.time() * 1000)
            # Guard against partially formed OHLCV candle.
            finished = [bar for bar in rows if int(bar[0]) + 300_000 <= now_ms]
            if len(finished) < 40:
                raise ValueError("Insufficient closed LBank futures candles")
            ind = indicators(finished)
            if now_ms - ind["closed_at"] > MAX_STALENESS_MS:
                raise ValueError("Stale exchange candle data")
            if ind["closed_at"] != last_bar:
                last_bar = ind["closed_at"]
                side = signal(ind)
                LOG.info("closed=%s price=%.2f rsi=%.1f volratio=%.2f signal=%s",
                         datetime.fromtimestamp(last_bar / 1000, tz=timezone.utc),
                         ind["price"], ind["rsi"], ind["volume_ratio"], side)
                if side and time.time() - last_alert.get(side, 0) >= COOLDOWN:
                    message = ("[LBank BTCUSDT perpetual | observation only]\n"
                               f"{side} | close {ind['price']:.1f}\n"
                               f"RSI {ind['rsi']:.1f} | volume 5m/20avg {ind['volume_ratio']:.2f}x\n"
                               f"MACD {ind['macd']:.2f} / signal {ind['signal']:.2f}\n"
                               "NOT an entry order. Check liquidity, risk and current price.")
                    if telegram(message):
                        last_alert[side] = time.time()
        except Exception:
            LOG.exception("LBank market-data monitor error (no trading action)")
        time.sleep(POLL)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    run()
