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
from datetime import datetime, timezone, timedelta

import ccxt

LOG = logging.getLogger("lbank_monitor")
SYMBOL = "BTC/USDT:USDT"
POLL = max(30, int(os.getenv("LBANK_POLL_SECONDS", "60")))
COOLDOWN = max(2700, int(os.getenv("LBANK_ALERT_COOLDOWN_SECONDS", "2700")))
REVERSAL_COOLDOWN = 5400
MAX_DAILY_ALERTS = 8
STARTUP_RETRY = 300
STARTUP_TEST_ENABLED = os.getenv("LBANK_STARTUP_TEST_ENABLED", "false").lower() == "true"
ENTRY_CONFIRM_MAX_AGE_SECONDS = 240
ENTRY_MAX_CHASE_PCT = 0.0010

MAX_AGE_MS = 180_000
# Configured values are estimates, not authenticated account fee/risk records.
MAKER_FEE = 0.0002  # 0.02% if the LIMIT order actually adds liquidity
TAKER_FEE = 0.0006  # 0.06% for an immediately executed order
STOP_SLIPPAGE = 0.0002  # estimated adverse slippage 0.02% when stop executes
# Entry=resting maker LIMIT; take-profit=resting maker LIMIT; stop=taker.
# If entry or TP crosses the spread, the fill may incur Taker fees.
# These model assumptions require confirmation from actual fill history.
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


def net_pnl_per_btc(direction, entry, exit_price, *, exit_kind="stop"):
    """Linear USDT PnL per BTC: maker entry + maker TP or taker STOP.

    Maker fees require passive fills; stop slippage is merely an estimate.
    No actual executions or live funding quote are accessible.
    """
    if exit_kind not in ("stop", "take_profit"):
        raise ValueError("exit_kind must be stop or take_profit")
    actual_entry = entry  # only if resting limit fills as Maker
    actual_exit = (exit_price * (1 - direction * STOP_SLIPPAGE)
                   if exit_kind == "stop" else exit_price)
    gross = direction * (actual_exit - actual_entry)
    exit_fee = TAKER_FEE if exit_kind == "stop" else MAKER_FEE
    fees = actual_entry * MAKER_FEE + actual_exit * exit_fee
    reserve = entry * FUNDING_BUFFER
    return gross - fees - reserve

def minimum_net_rr_target(direction, entry, unit_loss, ratio):
    """Solve for price needed to achieve a specific NET R:R, not forecast."""
    low, high = 0.0, entry * 0.05
    target_net = unit_loss * ratio
    if net_pnl_per_btc(direction, entry, entry + direction * high, exit_kind="take_profit") < target_net:
        return None
    for _ in range(55):
        mid = (low + high) / 2
        if net_pnl_per_btc(direction, entry, entry + direction * mid, exit_kind="take_profit") < target_net:
            low = mid
        else:
            high = mid
    return entry + direction * high


def calculate_plan(prices, direction):
    """Cost-aware hypothetical levels for a candidate, not a price prediction."""
    if len(prices) < MIN_SAMPLES:
        return None
    curr = prices[-1]
    momentum5 = (curr / prices[-6] - 1) * 100
    momentum15 = (curr / prices[-16] - 1) * 100
    strength = rsi(prices)
    # Price-based local invalidation, derived from last eight sampled prices.
    local = prices[-9:-1]
    stop = (min(curr * (1 - .0025), min(local) * (1 - .0003))
            if direction == 1 else
            max(curr * (1 + .0025), max(local) * (1 + .0003)))
    stop_distance = abs(curr - stop) / curr
    if not (.0025 <= stop_distance <= .0060):
        return None
    unit_loss = -net_pnl_per_btc(direction, curr, stop)
    if unit_loss <= 0:
        return None
    t1 = minimum_net_rr_target(direction, curr, unit_loss, MIN_NET_RR)
    t2 = minimum_net_rr_target(direction, curr, unit_loss, 2.5)
    if t1 is None or t2 is None or t1 <= 0 or t2 <= 0:
        return None
    # Required profit must be at least plausible compared with sampled movement,
    # otherwise skip instead of inventing a reachable price target.
    observed_range = max(prices[-60:]) - min(prices[-60:])
    if abs(t1 - curr) > 1.5 * observed_range:
        return None
    permitted_loss = REF_BALANCE * RISK_FRACTION
    effective_entry = curr
    qty = min(permitted_loss / unit_loss,
              REF_BALANCE * MAX_MARGIN_FRACTION * LEVERAGE / effective_entry)
    if qty <= 0:
        return None
    net_loss = -qty * net_pnl_per_btc(direction, curr, stop)
    net_gain1 = qty * net_pnl_per_btc(direction, curr, t1, exit_kind="take_profit")
    net_gain2 = qty * net_pnl_per_btc(direction, curr, t2, exit_kind="take_profit")
    if net_loss <= 0 or net_gain1 / net_loss < MIN_NET_RR - 1e-7:
        return None
    return {
        "direction": direction, "side": "롱" if direction == 1 else "숏",
        "price": curr, "rsi": strength, "m5": momentum5, "m15": momentum15,
        "stop": stop, "target1": t1, "target2": t2,
        "margin": qty * effective_entry / LEVERAGE, "notional": qty * curr,
        "net_loss": net_loss, "net_gain1": net_gain1, "net_gain2": net_gain2,
        "rr1": net_gain1 / net_loss, "rr2": net_gain2 / net_loss,
        "fee_roundtrip_est": qty * (effective_entry * MAKER_FEE + t1 * MAKER_FEE),
        "fee_stop_est": qty * (effective_entry * MAKER_FEE +
            stop * (1 - direction * STOP_SLIPPAGE) * TAKER_FEE),
        "taker_fallback_extra_fee": qty * (effective_entry * (TAKER_FEE - MAKER_FEE)
            + t1 * (TAKER_FEE - MAKER_FEE)),
    }


def classify(prices):
    """Identify a breakout WATCH; not yet actionable as an entry check."""
    if len(prices) < MIN_SAMPLES:
        return None
    curr = prices[-1]
    m5 = (curr / prices[-6] - 1) * 100
    m15 = (curr / prices[-16] - 1) * 100
    macd_arr = [a - b for a, b in zip(ema(prices, 12), ema(prices, 26))]
    strength = rsi(prices)
    macd, sig = macd_arr[-1], ema(macd_arr, 9)[-1]
    if curr > max(prices[-16:-1]) and macd > sig and 55 <= strength <= 80 and m5 >= .14 and m15 >= .20:
        direction = 1
    elif curr < min(prices[-16:-1]) and macd < sig and 20 <= strength <= 45 and m5 <= -.14 and m15 <= -.20:
        direction = -1
    else:
        return None
    return calculate_plan(prices, direction)


def confirm_watch(prices, watch, age_seconds):
    """Separate second-stage ENTRY REVIEW with fresh price confirmation.

    Used for user-side manual checking only, not a guaranteed market entry.
    It never assumes orderbook depth or authentic exchange OHLCV bars.
    """
    if watch is None or age_seconds < 30 or age_seconds > ENTRY_CONFIRM_MAX_AGE_SECONDS:
        return None
    if len(prices) < MIN_SAMPLES:
        return None
    direction = watch["direction"]
    current = prices[-1]
    initial = watch["price"]
    relative = direction * (current / initial - 1)
    # Do not chase >0.10% in the favorable direction or accept >0.10% failed breakout.
    if relative < -ENTRY_MAX_CHASE_PCT or relative > ENTRY_MAX_CHASE_PCT:
        return None
    m5 = (current / prices[-6] - 1) * 100
    m15 = (current / prices[-16] - 1) * 100
    macd_line = [a - b for a, b in zip(ema(prices, 12), ema(prices, 26))]
    macd, sig = macd_line[-1], ema(macd_line, 9)[-1]
    strength = rsi(prices)
    if direction == 1:
        valid = macd > sig and strength >= 50 and m5 >= .04 and m15 >= .10
    else:
        valid = macd < sig and strength <= 50 and m5 <= -.04 and m15 <= -.10
    if not valid:
        return None
    return calculate_plan(prices, direction)


def format_entry_alert(plan, observed_at_utc):
    daily_goal = REF_BALANCE * 0.10
    return (
        f"[LBank BTCUSDT 선물 | 조건부 진입 검토 - {plan['side']}]\n"
        "※ 1분 간격 선물 가격 표본에서 2단계 확인한 관찰 신호\n"
        f"확인 시각(UTC): {observed_at_utc}\n"
        f"현재 참고가격: {plan['price']:,.1f} USDT\n"
        f"손절 참고가: {plan['stop']:,.1f}\n"
        f"목표1 필요가격(순손익비 {plan['rr1']:.2f}): {plan['target1']:,.1f}\n"
        f"목표2 필요가격(순손익비 {plan['rr2']:.2f}): {plan['target2']:,.1f}\n"
        f"증거금 한도 예시: {plan['margin']:.2f} USDT / 명목금액: {plan['notional']:.2f} USDT\n"
        f"손절 예상 순손실: -{plan['net_loss']:.2f} USDT\n"
        f"목표1 예상 순이익: +{plan['net_gain1']:.2f} USDT\n"
        f"목표2 예상 순이익: +{plan['net_gain2']:.2f} USDT\n"
        f"정상 익절 왕복 Maker 수수료 추정: {plan['fee_roundtrip_est']:.2f} USDT\n"
        f"손절 시 Maker진입/Taker청산 수수료 추정: {plan['fee_stop_est']:.2f} USDT\n"
        f"진입·익절 모두 즉시 Taker 체결되면 추가 수수료 약 {plan['taker_fallback_extra_fee']:.2f} USDT\n"
        f"기준 시드: {REF_BALANCE:.0f} USDT / 일 순수익 10% 목표: {daily_goal:.2f} USDT (보장 불가)\n"
        f"5분 가격표본 변화 {plan['m5']:+.2f}% | 15분 {plan['m15']:+.2f}% | 표본 RSI {plan['rsi']:.1f}\n"
        "조건부 수수료: 수동 지정가 Maker진입 0.02%/Maker익절 0.02%, 손절 Taker 0.06%\n"
        "손절 슬리피지 0.02% 가정/펀딩 불리할 때 0.01% 완충(실제 비용 다를 수 있음)\n"
        "※ 지정가라도 즉시 체결되면 Taker 가능, Post-Only는 미체결 위험\n"
        "목표가는 예측이 아닌 비용·손익비 계산값. 실제 호가·체결·펀딩 미확인.\n"
        "수동 거래 전 거래소 5분봉/15분봉과 호가·손절 주문을 반드시 확인하세요."
    )


def alert_gate(now, side, state):
    day = datetime.fromtimestamp(now, tz=timezone(timedelta(hours=9))).date().isoformat()
    if day != state["day"]:
        state["day"], state["count"] = day, 0
    if state["count"] >= MAX_DAILY_ALERTS:
        return "daily_cap"
    if state["last_time"] is not None:
        elapsed = now - state["last_time"]
        if elapsed < COOLDOWN:
            return "global_cooldown"
        if side != state["last_side"] and elapsed < REVERSAL_COOLDOWN:
            return "reverse_cooldown"
    return "ok"


def record_alert(now, side, state):
    if alert_gate(now, side, state) != "ok":
        raise ValueError("alert gate disallowed sending")
    state["last_time"], state["last_side"] = now, side
    state["count"] += 1


def run():
    exchange = ccxt.lbank({"enableRateLimit": True, "timeout": 12000,
                           "options": {"defaultType": "swap"}})
    exchange.load_markets()
    if not exchange.market(SYMBOL).get("swap"):
        raise RuntimeError("Refuse to monitor spot instrument")
    LOG.info("LBank BTCUSDT: TWO-STAGE fee-aware entry-review monitor started. REF_BALANCE=%.2f risk_pct=%.2f; NO orders",
             REF_BALANCE, 100 * RISK_FRACTION)
    observations = deque(maxlen=120)
    watch = None
    alert_state = {"day": None, "count": 0, "last_time": None, "last_side": None}
    last_sample = 0
    startup_done = False
    next_startup_test = 0
    counters = {"watched": 0, "confirmed": 0, "expired": 0, "blocked": 0, "suppressed": 0}
    next_log_summary = time.time() + 3600
    while True:
        try:
            price, change24 = get_perpetual_snapshot(exchange)
            now = time.time()
            if STARTUP_TEST_ENABLED and not startup_done and now >= next_startup_test:
                startup_done = telegram("[LBank BTCUSDT] 2단계 감시 재시작. 시험 알림이며 진입 신호 아님.")
                LOG.info("Telegram startup test %s", "SENT" if startup_done else "FAILED")
                next_startup_test = now + STARTUP_RETRY
            if now - last_sample >= POLL:
                last_sample = now
                observations.append((now, price))
                prices = [v for _, v in observations]
                recent = list(observations)[-MIN_SAMPLES:]
                continuous = len(recent) >= MIN_SAMPLES and all(
                    20 <= recent[i][0] - recent[i-1][0] <= max(POLL * 2.5, 150)
                    for i in range(1, len(recent)))
                if not continuous:
                    watch = None
                elif watch is not None:
                    age = now - watch["time"]
                    if age > ENTRY_CONFIRM_MAX_AGE_SECONDS:
                        watch = None
                        counters["expired"] += 1
                        LOG.info("watch expired; no ENTRY REVIEW signal")
                    else:
                        plan = confirm_watch(prices, watch["plan"], age)
                        if plan is not None:
                            side = plan["side"]
                            if alert_gate(now, side, alert_state) == "ok":
                                message = format_entry_alert(
                                    plan, datetime.fromtimestamp(now, tz=timezone.utc).isoformat())
                                if telegram(message):
                                    record_alert(now, side, alert_state)
                                    counters["confirmed"] += 1
                                    LOG.info("ENTRY REVIEW SENT side=%s entry=%.1f stop=%.1f t1=%.1f net_rr=%.2f",
                                             side, plan["price"], plan["stop"], plan["target1"], plan["rr1"])
                                else:
                                    LOG.warning("ENTRY REVIEW SEND FAILED")
                            else:
                                counters["suppressed"] += 1
                                LOG.info("ENTRY REVIEW suppressed by cooldown side=%s", side)
                            watch = None
                if continuous and watch is None:
                    proposal = classify(prices)
                    if proposal is not None:
                        side = proposal["side"]
                        if alert_gate(now, side, alert_state) == "ok":
                            watch = {"plan": proposal, "time": now}
                            counters["watched"] += 1
                            LOG.info("watch created %s at %.1f (no preliminary Telegram noise)",
                                     side, proposal["price"])
                        else:
                            counters["suppressed"] += 1
                    else:
                        counters["blocked"] += 1
                LOG.info("contract=BTCUSDT price=%.1f count=%d watch=%s confirmed_total=%d",
                         price, len(prices), watch["plan"]["side"] if watch else "none",
                         counters["confirmed"])
                if now >= next_log_summary:
                    LOG.info("ALERT FUNNEL last-hour totals: %s", counters)
                    counters = {key: 0 for key in counters}
                    next_log_summary = now + 3600
        except Exception as exc:
            LOG.error("LBank market error (%s): %s", type(exc).__name__, str(exc)[:160])
        time.sleep(POLL)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    run()
