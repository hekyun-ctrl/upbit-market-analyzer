"""LBank BTCUSDT perpetual-only public price observation monitor.

Never creates orders or reads private LBank accounts. Since LBank's documented
contract API lacks native OHLCV candles and CCXT routes fetch_ohlcv to SPOT,
this monitor explicitly uses only actual LBank perpetual TICKER snapshots.
Signals use sampled 1-minute prices (NOT trade candles or trade volume).
"""
import json
import logging
import os
import time
import urllib.parse
import urllib.request
from collections import deque
from datetime import datetime, timezone

import ccxt

LOG = logging.getLogger("lbank_monitor")
SYMBOL = "BTC/USDT:USDT"
POLL = max(30, int(os.getenv("LBANK_POLL_SECONDS", "60")))
COOLDOWN = max(900, int(os.getenv("LBANK_ALERT_COOLDOWN_SECONDS", "1200")))
STARTUP_RETRY = 300
MAX_AGE_MS = 180_000


def ema(values, period):
    alpha = 2 / (period + 1)
    current = values[0]
    result = []
    for value in values:
        current = alpha * value + (1 - alpha) * current
        result.append(current)
    return result


def rsi(values, period=14):
    changes = [values[i] - values[i-1] for i in range(1, len(values))]
    ups = [max(0, x) for x in changes]
    downs = [max(0, -x) for x in changes]
    up = sum(ups[:period]) / period
    down = sum(downs[:period]) / period
    for u, d in zip(ups[period:], downs[period:]):
        up = (up * (period - 1) + u) / period
        down = (down * (period - 1) + d) / period
    return 100 if down == 0 else 100 - 100 / (1 + up / down)


def telegram(message):
    token = os.getenv("TELEGRAM_BOT_TOKEN", "")
    chat = os.getenv("TELEGRAM_CHAT_ID", "")
    if not token or not chat:
        LOG.error("Telegram credentials missing; cannot send")
        return False
    payload = urllib.parse.urlencode({"chat_id": chat, "text": message}).encode()
    url = "https://api.telegram.org/bot" + token + "/sendMessage"
    # Never log URL or token, including urllib exception values.
    try:
        with urllib.request.urlopen(urllib.request.Request(
                url, data=payload, method="POST"), timeout=12) as resp:
            return bool(json.load(resp).get("ok"))
    except Exception as exc:
        LOG.error("Telegram send failed, exception type=%s", type(exc).__name__)
        return False


def get_perpetual_snapshot(exchange):
    ticker = exchange.fetch_ticker(SYMBOL)
    if not ticker or ticker.get("symbol") != SYMBOL:
        raise ValueError("LBank returned wrong futures ticker symbol")
    price = float(ticker["last"])
    if not (price > 0):
        raise ValueError("LBank returned invalid contract last price")
    timestamp = ticker.get("timestamp")
    if timestamp and abs(int(time.time() * 1000) - int(timestamp)) > MAX_AGE_MS:
        raise ValueError("Stale LBank perpetual ticker")
    return price, ticker.get("percentage")


def classify(prices):
    # This is a price-snapshot momentum WATCH, not a 5m OHLCV trading signal.
    if len(prices) < 40:
        return None
    curr = prices[-1]
    m5 = (curr / prices[-6] - 1) * 100
    m15 = (curr / prices[-16] - 1) * 100
    macd_arr = [a - b for a, b in zip(ema(prices, 12), ema(prices, 26))]
    macd = macd_arr[-1]
    sig = ema(macd_arr, 9)[-1]
    strength = rsi(prices)
    high = max(prices[-16:-1])
    low = min(prices[-16:-1])
    if curr > high and macd > sig and 55 <= strength <= 80 and m5 >= .14 and m15 >= .20:
        side = "롱 관찰"
    elif curr < low and macd < sig and 20 <= strength <= 45 and m5 <= -.14 and m15 <= -.20:
        side = "숏 관찰"
    else:
        return None
    # Reference stop is a conservative *hypothetical* position-risk illustration.
    risk = max(curr * .0018, abs(m15 / 100 * curr) * .35)
    if risk / curr > .0045:
        return None
    direction = 1 if side.startswith("롱") else -1
    stop = curr - direction * risk
    return {
        "side": side, "price": curr, "rsi": strength, "macd": macd,
        "signal": sig, "m5": m5, "m15": m15,
        "stop": stop, "target1": curr + direction * risk * 1.5,
        "target2": curr + direction * risk * 2.5,
    }


def run():
    exchange = ccxt.lbank({"enableRateLimit": True, "timeout": 12000,
                           "options": {"defaultType": "swap"}})
    exchange.load_markets()
    if not exchange.market(SYMBOL).get("swap"):
        raise RuntimeError("Refuse to monitor spot instrument")
    LOG.info("LBank BTCUSDT PERPETUAL ticker monitor started (no trade permissions)")
    observations = deque(maxlen=120)
    last_alert = {}
    last_sample = 0
    next_startup_test = 0
    startup_done = False
    while True:
        try:
            price, change24 = get_perpetual_snapshot(exchange)
            now = time.time()
            if not startup_done and now >= next_startup_test:
                note = (f"[LBank 선물 BTCUSDT] 연결 시험 알림\n"
                        f"선물 현재가: {price:,.1f} USDT\n"
                        "서버 가동 및 텔레그램 연결 확인.\n"
                        "가격 표본 수집 중이며 아직 진입 신호가 아닙니다.\n"
                        "실제 주문은 자동으로 실행하지 않습니다.")
                startup_done = telegram(note)
                LOG.info("Telegram startup test %s", "SENT" if startup_done else "FAILED")
                next_startup_test = now + STARTUP_RETRY
            if now - last_sample >= POLL:
                last_sample = now
                observations.append((now, price))
                prices = [v for _, v in observations]
                # Reject gaps: the most recent 40 observations must be continuous.
                recent = list(observations)[-40:]
                continuous = len(recent) >= 40 and all(
                    20 <= recent[i][0] - recent[i-1][0] <= max(POLL * 2.5, 150)
                    for i in range(1, len(recent))
                )
                result = classify(prices) if continuous else None
                LOG.info("contract=BTCUSDT sampled_price=%.1f samples=%d signal=%s",
                         price, len(prices), result["side"] if result else "none")
                if result:
                    side = result["side"]
                    if now - last_alert.get(side, 0) >= COOLDOWN:
                        msg = (f"[LBank BTCUSDT 무기한 선물 | {side}]\n"
                               "※ LBank 선물 실가격의 1분 표본 기반, 캔들 아님\n"
                               f"가격/관심가: {result['price']:,.1f}\n"
                               f"참고 손절: {result['stop']:,.1f}\n"
                               f"참고 목표1: {result['target1']:,.1f}\n"
                               f"참고 목표2: {result['target2']:,.1f}\n"
                               f"5분변화: {result['m5']:+.2f}% | 15분변화: {result['m15']:+.2f}%\n"
                               f"표본 RSI: {result['rsi']:.1f}\n"
                               "주문 신호가 아닌 관찰 알림입니다. 50배 격리 거래 주의.")
                        if telegram(msg):
                            last_alert[side] = now
                            LOG.info("Telegram signal alert SENT: %s", side)
                        else:
                            LOG.warning("Telegram signal alert FAILED")
        except Exception as exc:
            LOG.error("LBank perpetual price error (%s): %s",
                      type(exc).__name__, str(exc)[:200])
        time.sleep(POLL)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    run()
