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
# Configured values are estimates, not authenticated account fee/risk records.
FEE_IN = 0.0006   # LBank public standard market-order Taker: 0.06%
FEE_OUT = 0.0006  # Conservative stop or close: Taker 0.06%
SLIPPAGE_EACH = 0.0002  # Assumed adverse fill on BOTH entry and exit (0.02% each)
FUNDING_BUFFER = 0.0001  # Hypothetical 0.01% adverse funding reserve; actual rate varies
REF_BALANCE = float(os.getenv("LBANK_REFERENCE_BALANCE_USDT", "958"))
RISK_FRACTION = float(os.getenv("LBANK_RISK_PER_TRADE_PCT", "0.5")) / 100
LEVERAGE = float(os.getenv("LBANK_REFERENCE_LEVERAGE", "50"))
MAX_MARGIN_FRACTION = 0.10
MIN_NET_RR = 1.5
MIN_SAMPLES = 60
if not (0 < REF_BALANCE <= 1000000 and 0 < RISK_FRACTION <= 0.01 and
        1 <= LEVERAGE <= 50):
    raise ValueError("Unsafe risk configuration: check balance, max 1% risk and max 50x")


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


def net_pnl_per_btc(direction, entry, exit_price):
    """Linear USDT-per-BTC net PnL for conservative adverse fills + Taker fees.

    This is a cost MODEL, not a verified live execution price or funding quote.
    """
    actual_entry = entry * (1 + direction * SLIPPAGE_EACH)
    actual_exit = exit_price * (1 - direction * SLIPPAGE_EACH)
    gross = direction * (actual_exit - actual_entry)
    fees = actual_entry * FEE_IN + actual_exit * FEE_OUT
    reserve = entry * FUNDING_BUFFER
    return gross - fees - reserve


def minimum_net_rr_target(direction, entry, unit_loss, ratio):
    """Solve for price needed to achieve a specific NET R:R, not forecast."""
    low, high = 0.0, entry * 0.05
    target_net = unit_loss * ratio
    if net_pnl_per_btc(direction, entry, entry + direction * high) < target_net:
        return None
    for _ in range(55):
        mid = (low + high) / 2
        if net_pnl_per_btc(direction, entry, entry + direction * mid) < target_net:
            low = mid
        else:
            high = mid
    return entry + direction * high


def classify(prices):
    """Create cost-aware *observation* alerts from evenly-spaced price samples.

    No OHLCV candles, order-book spread, liquidation prices, or trading volume.
    Forecasted market outcomes are intentionally NOT claimed.
    """
    if len(prices) < MIN_SAMPLES:
        return None
    curr = prices[-1]
    m5 = (curr / prices[-6] - 1) * 100
    m15 = (curr / prices[-16] - 1) * 100
    macd_arr = [a - b for a, b in zip(ema(prices, 12), ema(prices, 26))]
    macd = macd_arr[-1]
    sig = ema(macd_arr, 9)[-1]
    strength = rsi(prices)
    prior_high = max(prices[-16:-1])
    prior_low = min(prices[-16:-1])
    if curr > prior_high and macd > sig and 55 <= strength <= 80 and m5 >= .14 and m15 >= .20:
        side, direction = "롱 관찰", 1
        # Use a sampled local structure plus a minimum buffer, not arbitrary RR-only stop.
        stop = min(curr * (1 - .0025), min(prices[-16:-1]) * (1 - .0003))
    elif curr < prior_low and macd < sig and 20 <= strength <= 45 and m5 <= -.14 and m15 <= -.20:
        side, direction = "숏 관찰", -1
        stop = max(curr * (1 + .0025), max(prices[-16:-1]) * (1 + .0003))
    else:
        return None
    stop_distance = abs(curr - stop) / curr
    if not (.0025 <= stop_distance <= .0060):
        return None
    loss_unit = -net_pnl_per_btc(direction, curr, stop)
    if loss_unit <= 0:
        return None
    target1 = minimum_net_rr_target(direction, curr, loss_unit, MIN_NET_RR)
    target2 = minimum_net_rr_target(direction, curr, loss_unit, 2.5)
    if target1 is None or target2 is None or target1 <= 0 or target2 <= 0:
        return None
    # Reject unrealistically distant required-profit targets compared with
    # the observable *price snapshot* range; this is not a predictive model.
    range_60 = max(prices[-60:]) - min(prices[-60:])
    if abs(target1 - curr) > 1.5 * range_60:
        return None

    permitted_loss = REF_BALANCE * RISK_FRACTION
    qty_by_loss = permitted_loss / loss_unit
    effective_entry = curr * (1 + direction * SLIPPAGE_EACH)
    qty_by_margin = (REF_BALANCE * MAX_MARGIN_FRACTION * LEVERAGE) / effective_entry
    qty = min(qty_by_loss, qty_by_margin)
    if qty <= 0:
        return None
    net_loss = -qty * net_pnl_per_btc(direction, curr, stop)
    net_gain1 = qty * net_pnl_per_btc(direction, curr, target1)
    net_gain2 = qty * net_pnl_per_btc(direction, curr, target2)
    if net_loss <= 0 or net_gain1 / net_loss < MIN_NET_RR - 1e-7:
        return None
    return {
        "side": side, "price": curr, "rsi": strength, "m5": m5, "m15": m15,
        "stop": stop, "target1": target1, "target2": target2,
        "max_loss": permitted_loss, "notional": qty * curr,
        "margin": qty * effective_entry / LEVERAGE,
        "net_loss": net_loss, "net_gain1": net_gain1, "net_gain2": net_gain2,
        "rr1": net_gain1 / net_loss, "rr2": net_gain2 / net_loss,
        "fee_roundtrip_est": qty * (
            effective_entry * FEE_IN
            + target1 * (1 - direction * SLIPPAGE_EACH) * FEE_OUT
        ),
    }


def run():
    exchange = ccxt.lbank({"enableRateLimit": True, "timeout": 12000,
                           "options": {"defaultType": "swap"}})
    exchange.load_markets()
    if not exchange.market(SYMBOL).get("swap"):
        raise RuntimeError("Refuse to monitor spot instrument")
    LOG.info("LBank BTCUSDT PERPETUAL COST-AWARE observation monitor started; ref_balance=%.2f, risk_pct=%.2f, maker assumed NO, taker each=%.4f, slip each=%.4f, funding buffer=%.4f; no trade permissions",
             REF_BALANCE, RISK_FRACTION * 100, FEE_IN, SLIPPAGE_EACH, FUNDING_BUFFER)
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
                               f"관찰 시점 가격(진입 확정 아님): {result['price']:,.1f}\n"
                               f"참고 손절: {result['stop']:,.1f}\n"
                               f"필요 목표1(순손익비 {result['rr1']:.2f}): {result['target1']:,.1f}\n"
                               f"필요 목표2(순손익비 {result['rr2']:.2f}): {result['target2']:,.1f}\n"
                               f"추정 증거금: {result['margin']:.2f} USDT / 명목금액: {result['notional']:.2f} USDT\n"
                               f"손절 예상 순손실: -{result['net_loss']:.2f} USDT\n"
                               f"목표1 예상 순이익: +{result['net_gain1']:.2f} USDT\n"
                               f"목표2 예상 순이익: +{result['net_gain2']:.2f} USDT\n"
                               f"예상 왕복 거래 수수료(목표1 기준): {result['fee_roundtrip_est']:.2f} USDT\n"
                               f"참고 잔고 {REF_BALANCE:.0f} USDT / 1회 최대 손실 예산 {result['max_loss']:.2f} USDT\n"
                               "가정: 시장가 왕복 각 0.06%, 불리한 슬리피지 각 0.02%, 펀딩 완충 0.01%\n"
                               f"5분변화: {result['m5']:+.2f}% | 15분변화: {result['m15']:+.2f}%\n"
                               f"표본 RSI: {result['rsi']:.1f}\n"
                               "목표가는 해당 순손익비를 충족할 최소 필요가격일 뿐 예측치가 아닙니다.\n"
                               "실제 계정 수수료·펀딩·호가·슬리피지는 미확인.\n"
                               "1분 가격표본 관찰 알림이며 매수/매도 지시가 아닙니다. 50배 격리 주의.")
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
