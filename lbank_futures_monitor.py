"""LBank BTCUSDT perpetual-only public price observation monitor.

Never creates orders or reads private LBank accounts. Since LBank's documented
contract API lacks native OHLCV candles and CCXT routes fetch_ohlcv to SPOT,
this monitor explicitly uses only actual LBank perpetual TICKER snapshots.
Signals use sampled 1-minute prices (NOT trade candles or trade volume).
Persistent observations across short restarts are still TICKER samples, not candles.
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
from lbank_signal_risk import SignalRiskTracker, format_risk_alert
from lbank_telegram_positions import PositionState, TelegramBot
from lbank_sample_store import load_snapshots, save_snapshots
from lbank_structure_patterns import detect_structure, confirm_structure
from lbank_sampled_context import sampled_context_gate

LOG = logging.getLogger("lbank_monitor")
SYMBOL = "BTC/USDT:USDT"
POLL = max(30, int(os.getenv("LBANK_POLL_SECONDS", "60")))
RISK_POLL = max(10, min(POLL, int(os.getenv("LBANK_RISK_POLL_SECONDS", "15"))))
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
# User's manual entry can cross the spread, and conditional LBank TP/SL close at
# MARKET; use conservative Taker on BOTH legs. Passive limit orders may cost less.
# Actual personal fee tier, funding and slippage must be checked in fill history.
FUNDING_BUFFER = 0.0001  # Hypothetical 0.01% adverse funding reserve; actual rate varies
REF_BALANCE = float(os.getenv("LBANK_REFERENCE_BALANCE_USDT", "958"))
RISK_FRACTION = float(os.getenv("LBANK_RISK_PER_TRADE_PCT", "0.5")) / 100
LEVERAGE = float(os.getenv("LBANK_REFERENCE_LEVERAGE", "50"))
MAX_MARGIN_FRACTION = 0.10
MIN_NET_RR = 1.5
MIN_SAMPLES = 60
SAMPLE_STORE_PATH = os.getenv("LBANK_SAMPLE_STORE_PATH", "/data/lbank_price_samples.json")
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



def sampled_five_minute_rsi(observations):
    """RSI(14) from COMPLETED 5-minute buckets of LBank perpetual ticker samples.

    This is NOT authentic exchange OHLCV RSI. Require timestamp continuity,
    a late-in-bucket quote and a fully closed final bucket. Never mix spot data.
    Missing or stale buckets cause entry alerts to fail closed.
    """
    if len(observations) < 85:
        return None
    final_bucket = int(observations[-1][0] // 300) - 1
    closes_by_bucket = {}
    for timestamp, price in observations:
        bucket = int(timestamp // 300)
        if bucket <= final_bucket and timestamp - bucket * 300 >= 210:
            closes_by_bucket[bucket] = float(price)
    buckets = sorted(closes_by_bucket)[-60:]
    if (len(buckets) < 20 or buckets[-1] != final_bucket or
            any(buckets[i] != buckets[i - 1] + 1 for i in range(1, len(buckets)))):
        return None
    closes = [closes_by_bucket[b] for b in buckets]
    rsi_values = [rsi(closes[:i], 14) for i in range(15, len(closes) + 1)]
    return {"closes": closes, "rsi": rsi_values}



def confirmed_sampled_5m_oversold_rebound(observations):
    """Wait for a genuinely turning 5m *ticker-sampled* RSI, not a touch of 30.

    Designed for range-support LONG entries only; do not present as real
    exchange OHLCV RSI. Require two consecutive COMPLETE 5m RSI improvements
    and higher sampled closes after a confirmed RSI <= 30 in recent history.
    Heuristic thresholds must be validated prospectively, not assumed 95%.
    """
    state = sampled_five_minute_rsi(observations)
    if state is None:
        return False, "5m_oversold_data_unavailable", None
    values, closes = state["rsi"], state["closes"]
    current = values[-1]
    if len(values) < 14 or len(closes) < 20:
        return False, "5m_oversold_history_insufficient", current
    past_oversold = [i for i in range(max(0, len(values) - 12), len(values) - 2)
                     if values[i] <= 30]
    if not past_oversold:
        return False, "5m_oversold_not_observed", current
    if current <= 30:
        return False, "5m_oversold_still_falling_or_unrecovered", current
    if current >= 60:
        return False, "5m_oversold_rebound_already_extended", current
    if not (values[-1] > values[-2] > values[-3] and
            values[-1] - values[-3] >= 3.0):
        return False, "5m_rsi_two_completed_rises_missing", current
    if not (closes[-1] > closes[-2] > closes[-3] and
            closes[-1] >= min(closes[-6:]) * 1.0008):
        return False, "5m_price_two_completed_rebound_closes_missing", current
    return True, "5m_sampled_oversold_rebound_confirmed", current


def confirmed_sampled_5m_overbought_rejection(observations):
    """Overbought first, THEN two completed sampled 5m RSI and price declines.

    Range-resistance SHORT only. LBank perpetual ticker-derived closes are NOT
    real OHLCV bars; require a recent RSI >=70, confirmed weakening on two
    completed buckets and price rejection. No one-bar top picking.
    """
    state = sampled_five_minute_rsi(observations)
    if state is None:
        return False, "5m_overbought_data_unavailable", None
    values, closes = state["rsi"], state["closes"]
    current = values[-1]
    if len(values) < 14 or len(closes) < 20:
        return False, "5m_overbought_history_insufficient", current
    observed = [i for i in range(max(0, len(values) - 12), len(values) - 2)
                if values[i] >= 70]
    if not observed:
        return False, "5m_overbought_not_observed", current
    if current >= 70:
        return False, "5m_overbought_still_rising_or_not_rejected", current
    # Below 50 may already be late in a strong downside impulse.
    if current < 50:
        return False, "5m_overbought_rejection_already_extended", current
    if not (values[-1] < values[-2] < values[-3] and
            values[-3] - values[-1] >= 3.0):
        return False, "5m_rsi_two_completed_falls_missing", current
    if not (closes[-1] < closes[-2] < closes[-3] and
            closes[-1] <= max(closes[-6:]) * (1 - .0008)):
        return False, "5m_price_two_completed_rejection_closes_missing", current
    return True, "5m_sampled_overbought_rejection_confirmed", current


def five_minute_rsi_entry_gate(observations, direction, setup=None):
    """Guard against late chasing at RSI 30/70; wait for rebound or pullback.

    Only completed 5-min ticker-derived RSI values are used. These heuristics
    are conservative safeguards, NOT verified reversal probabilities.
    """
    state = sampled_five_minute_rsi(observations)
    if state is None:
        return False, "insufficient_closed_5m_samples", None
    values, closes = state["rsi"], state["closes"]
    current, previous = values[-1], values[-2]
    # A support bounce is NOT a long entry until completed sampled 5m RSI
    # actually crossed <=30 and subsequently turned up with price evidence.
    # Trend retests and breakouts remain separate strategies.
    if setup and setup.get("strategy") == "range_rebound":
        if direction == 1:
            rebound_ok, rebound_reason, _ = confirmed_sampled_5m_oversold_rebound(observations)
            if not rebound_ok:
                return False, rebound_reason, current
        elif direction == -1:
            rejection_ok, rejection_reason, _ = confirmed_sampled_5m_overbought_rejection(observations)
            if not rejection_ok:
                return False, rejection_reason, current
            # After a verified two-bar overbought rejection, avoid the prior
            # single-bar/RSI<=64 shortcut. Still respect fast RSI, higher-timeframe
            # context, structure and the full fee-aware risk gate elsewhere.
            return True, rejection_reason, current
    if current <= 30 or current >= 70:
        return False, "rsi_30_70_extreme_wait", current
    # Leave headroom for a bounce, including before a literal 30/70 touch.
    if direction == -1 and current < 36:
        return False, "short_near_oversold", current
    if direction == 1 and current > 64:
        return False, "long_near_overbought", current
    # Last 10 completed RSI periods (~50 minutes). Most recent extreme wins.
    start = max(0, len(values) - 10)
    extreme = None
    for i in range(start, len(values)):
        if values[i] <= 30:
            extreme = (i, "oversold")
        elif values[i] >= 70:
            extreme = (i, "overbought")
    if extreme is None:
        return True, "normal_zone", current
    hit, kind = extreme
    after = values[hit + 1:]
    if kind == "oversold":
        if direction == -1:
            # For SHORT: first a meaningful recovery to >=40, then a
            # completed 5m price / RSI rollover, not a short on the RSI touch.
            ready = (len(after) >= 2 and max(after) >= 40 and
                     current < previous and closes[-1] < closes[-2])
            return ready, ("oversold_rebound_then_rollover" if ready else
                           "wait_rebound_then_bearish_rejection"), current
        ready = (bool(after) and current >= 36 and
                 current > previous and closes[-1] > closes[-2])
        return ready, ("oversold_bounce_confirmed" if ready else
                       "wait_oversold_bounce"), current
    if direction == 1:
        # For LONG: let the overbought market pull back to <=60, then
        # confirm a subsequent 5m close / RSI uptick before chasing it.
        ready = (len(after) >= 2 and min(after) <= 60 and
                 current > previous and closes[-1] > closes[-2])
        return ready, ("overbought_pullback_then_support" if ready else
                       "wait_pullback_then_bullish_support"), current
    # A tightly scoped exception for genuine range-top bearish rejection.
    # A rollover off 70+ can be actionable before RSI reaches 64. Only
    # consider this when the structural resistance reaction already passed
    # and the last COMPLETED 5m sample is lower in both price and RSI.
    # Still forbid >=70 RSI and all late shorts after oversold extremes.
    if setup and setup.get("strategy") == "range_rebound" and direction == -1:
        prior_overbought = any(v >= 70 for v in values[-3:-1])
        early_rejection = (prior_overbought and 55 <= current <= 69.5 and
                           current < previous and closes[-1] < closes[-2])
        if early_rejection:
            return True, "overbought_range_rejection_confirmed", current
    ready = (bool(after) and current <= 64 and
             current < previous and closes[-1] < closes[-2])
    return ready, ("overbought_pullback_confirmed" if ready else
                   "wait_overbought_pullback"), current


def fast_sample_rsi_gate(prices, direction):
    """Extra safety: stop a fast sampled-RSI 28-type late SHORT or overheated LONG.

    This 1-minute ticker-based metric supplements the completed 5-minute gate,
    never substitutes for it. No assumption of deterministic RSI reversals.
    """
    strength = rsi(prices)
    if direction == -1 and strength <= 35:
        return False, "fast_rsi_short_oversold", strength
    if direction == 1 and strength >= 70:
        return False, "fast_rsi_long_overbought", strength
    return True, "fast_rsi_ok", strength


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
    """Linear USDT PnL per BTC, conservative Taker entry and Taker exit.

    Market-triggered TP/SL orders can slip; stop slippage is only a model
    estimate. No actual executions, personalized fees or funding are available.
    """
    if exit_kind not in ("stop", "take_profit"):
        raise ValueError("exit_kind must be stop or take_profit")
    actual_entry = entry
    actual_exit = (exit_price * (1 - direction * STOP_SLIPPAGE)
                   if exit_kind == "stop" else exit_price)
    gross = direction * (actual_exit - actual_entry)
    fees = actual_entry * TAKER_FEE + actual_exit * TAKER_FEE
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


def calculate_plan(prices, direction, setup=None):
    """Cost-aware hypothetical levels with optional structure-anchored stop."""
    setup = setup or {"strategy": "breakout"}
    strategy = setup.get("strategy", "breakout")
    if len(prices) < MIN_SAMPLES:
        return None
    curr = prices[-1]
    momentum5 = (curr / prices[-6] - 1) * 100
    momentum15 = (curr / prices[-16] - 1) * 100
    strength = rsi(prices)
    if strategy == "breakout":
        local = prices[-9:-1]
        stop = (min(curr * (1 - .0025), min(local) * (1 - .0003))
                if direction == 1 else
                max(curr * (1 + .0025), max(local) * (1 + .0003)))
        minimum_stop = .0025
    else:
        anchor = setup["structure_anchor"]
        stop = (min(curr * (1 - .0012), anchor - curr * .0002)
                if direction == 1 else
                max(curr * (1 + .0012), anchor + curr * .0002))
        minimum_stop = .0012
    stop_distance = abs(curr - stop) / curr
    if not (minimum_stop - 1e-10 <= stop_distance <= .0060):
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
    barrier = setup.get("barrier")
    if barrier is not None and direction * (barrier - t1) < curr * .00015:
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
        "strategy": strategy, "level": setup.get("level"),
        "structure_anchor": setup.get("structure_anchor"),
        "barrier": setup.get("barrier"),
        "price": curr, "rsi": strength, "m5": momentum5, "m15": momentum15,
        "stop": stop, "target1": t1, "target2": t2,
        "margin": qty * effective_entry / LEVERAGE, "notional": qty * curr,
        "net_loss": net_loss, "net_gain1": net_gain1, "net_gain2": net_gain2,
        "rr1": net_gain1 / net_loss, "rr2": net_gain2 / net_loss,
        "fee_roundtrip_est": qty * (effective_entry * TAKER_FEE + t1 * TAKER_FEE),
        "fee_stop_est": qty * (effective_entry * TAKER_FEE +
            stop * (1 - direction * STOP_SLIPPAGE) * TAKER_FEE),
        "maker_potential_savings": qty * (effective_entry + t1) * (TAKER_FEE - MAKER_FEE),
    }


def classify(prices, observations=None):
    """Identify a breakout WATCH; not yet actionable as an entry check."""
    if len(prices) < MIN_SAMPLES:
        return None
    curr = prices[-1]
    m5 = (curr / prices[-6] - 1) * 100
    m15 = (curr / prices[-16] - 1) * 100
    macd_arr = [a - b for a, b in zip(ema(prices, 12), ema(prices, 26))]
    strength = rsi(prices)
    macd, sig = macd_arr[-1], ema(macd_arr, 9)[-1]
    setup = detect_structure(prices, ema, rsi)
    if setup is not None:
        direction = setup["direction"]
    elif curr > max(prices[-16:-1]) and macd > sig and 55 <= strength <= 80 and m5 >= .14 and m15 >= .20:
        direction = 1
    elif curr < min(prices[-16:-1]) and macd < sig and 20 <= strength <= 45 and m5 <= -.14 and m15 <= -.20:
        direction = -1
    else:
        return None
    LOG.info("PREWATCH candidate strategy=%s direction=%s price=%.1f rsi1=%.1f",
             (setup or {}).get("strategy", "breakout"),
             "LONG" if direction == 1 else "SHORT", curr, strength)
    context = None
    if observations is not None:
        context_ok, context_reason, context = sampled_context_gate(
            observations, direction, (setup or {}).get("strategy", "breakout"))
        if not context_ok:
            LOG.info("CLOSED-BUCKET watch BLOCKED side=%s reason=%s context=%s",
                     "LONG" if direction == 1 else "SHORT", context_reason, context)
            return None
        allowed_fast, fast_reason, fast_rsi = fast_sample_rsi_gate(prices, direction)
        if not allowed_fast:
            LOG.info("Fast sampled RSI watch BLOCKED side=%s reason=%s rsi=%.1f",
                     "LONG" if direction == 1 else "SHORT", fast_reason, fast_rsi)
            return None
        allowed, reason, sampled_rsi5 = five_minute_rsi_entry_gate(observations, direction, setup=setup)
        if not allowed:
            LOG.info("5m sampled RSI watch BLOCKED side=%s reason=%s rsi=%s",
                     "LONG" if direction == 1 else "SHORT", reason, sampled_rsi5)
            return None
    else:
        sampled_rsi5 = None
    plan = calculate_plan(prices, direction, setup=setup)
    if plan is None:
        LOG.info("PREWATCH candidate cost/risk/target rejected strategy=%s direction=%s",
                 (setup or {}).get("strategy", "breakout"), direction)
    if plan is not None:
        plan["sampled_rsi5"] = sampled_rsi5
        plan["sampled_context"] = context
    return plan


def confirm_watch(prices, watch, age_seconds, observations=None):
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
    if watch.get("strategy", "breakout") != "breakout":
        valid = confirm_structure(prices, watch, ema, rsi)
    if not valid:
        return None
    context = None
    if observations is not None:
        context_ok, context_reason, context = sampled_context_gate(
            observations, direction, watch.get("strategy", "breakout"))
        if not context_ok:
            LOG.info("CLOSED-BUCKET confirm BLOCKED side=%s reason=%s context=%s",
                     "LONG" if direction == 1 else "SHORT", context_reason, context)
            return None
        allowed_fast, fast_reason, fast_rsi = fast_sample_rsi_gate(prices, direction)
        if not allowed_fast:
            LOG.info("Fast sampled RSI confirmation BLOCKED side=%s reason=%s rsi=%.1f",
                     "LONG" if direction == 1 else "SHORT", fast_reason, fast_rsi)
            return None
        allowed, reason, sampled_rsi5 = five_minute_rsi_entry_gate(observations, direction, setup=watch)
        if not allowed:
            LOG.info("5m sampled RSI confirmation BLOCKED side=%s reason=%s rsi=%s",
                     "LONG" if direction == 1 else "SHORT", reason, sampled_rsi5)
            return None
    else:
        sampled_rsi5 = None
    plan = calculate_plan(prices, direction, setup=watch)
    if plan is None:
        LOG.info("CONFIRM candidate cost/risk/target rejected strategy=%s direction=%s",
                 watch.get("strategy", "breakout"), direction)
    if plan is not None:
        plan["sampled_rsi5"] = sampled_rsi5
        plan["sampled_context"] = context
    return plan


def format_entry_alert(plan, observed_at_utc):
    daily_goal = REF_BALANCE * 0.10
    rsi5 = plan.get("sampled_rsi5")
    rsi5_text = "검증전" if rsi5 is None else f"{rsi5:.1f}"
    oversold_rebound_text = ("[반등형 롱] 완료된 5분 가격표본 RSI 30 이하 관찰 후 "
                            "2회 연속 RSI·종가 상승 확인 (거래소 실제 5분봉 아님)\n"
                            if plan.get("strategy") == "range_rebound" and
                               plan.get("direction") == 1 else "")
    overbought_rejection_text = ("[저항형 숏] 완료된 5분 가격표본 RSI 70 이상 관찰 후 "
                                 "2회 연속 RSI·종가 하락 확인 (거래소 실제 5분봉 아님)\n"
                                 if plan.get("strategy") == "range_rebound" and
                                    plan.get("direction") == -1 else "")
    context = plan.get("sampled_context") or {}
    context_text = (f"완료된 선물 가격표본: 5분 표본 15분 변화 {context['trend5_pct']:+.2f}% / "
                    f"15분 표본 45분 변화 {context['trend15_pct']:+.2f}% / "
                    f"최근 표본 변동폭 {context['range5_pct']:.2f}% / "
                    f"3단계 조정 후 반전 {'관찰' if context['three_step_recovery'] else '미확인'}\n"
                    if context else "선물 가격표본 다중시간대 검증: 미수행\n")
    pattern = {"breakout": "돌파", "range_rebound": "박스권 반등·저항",
               "trend_retest": "추세 눌림 재지지"}.get(plan.get("strategy"), "미분류")
    return (
        f"[LBank BTCUSDT 선물 | 조건부 진입 검토 - {plan['side']}]\n"
        "※ 1분 간격 선물 가격 표본에서 2단계 확인한 관찰 신호\n"
        f"확인 시각(UTC): {observed_at_utc}\n"
        f"탐지 패턴: {pattern} (실제 5분봉·호가 미확인)\n"
        f"현재 참고가격: {plan['price']:,.1f} USDT\n"
        f"손절 참고가: {plan['stop']:,.1f}\n"
        f"목표1 필요가격(순손익비 {plan['rr1']:.2f}): {plan['target1']:,.1f}\n"
        f"목표2 필요가격(순손익비 {plan['rr2']:.2f}): {plan['target2']:,.1f}\n"
        f"증거금 한도 예시: {plan['margin']:.2f} USDT / 명목금액: {plan['notional']:.2f} USDT\n"
        f"손절 예상 순손실: -{plan['net_loss']:.2f} USDT\n"
        f"목표1 예상 순이익: +{plan['net_gain1']:.2f} USDT\n"
        f"목표2 예상 순이익: +{plan['net_gain2']:.2f} USDT\n"
        f"시장가 가정 익절 왕복 Taker 수수료 추정: {plan['fee_roundtrip_est']:.2f} USDT\n"
        f"시장가 가정 손절 왕복 Taker 수수료 추정: {plan['fee_stop_est']:.2f} USDT\n"
        f"진입·익절 모두 Maker로 체결되면 절감 가능 추정: {plan['maker_potential_savings']:.2f} USDT\n"
        f"기준 시드: {REF_BALANCE:.0f} USDT / 일 순수익 10% 목표: {daily_goal:.2f} USDT (보장 불가)\n"
        f"5분 가격표본 변화 {plan['m5']:+.2f}% | 15분 {plan['m15']:+.2f}% | 1분 표본 RSI {plan['rsi']:.1f}\n"
        f"완료 5분 가격표본 RSI(거래소 실제 5분봉 RSI 아님): {rsi5_text}\n"
        f"{oversold_rebound_text}"
        f"{overbought_rejection_text}"
        f"{context_text}"
        "※ 3단계 반전은 가격표본의 마감값 비교이며 실제 캔들 3틱룰과 다름\n"
        "RSI 30/70 접촉 후 반등·조정과 재확인을 기다리는 필터 적용\n"
        "손익 계산: 진입/조건부 익절/손절 모두 Taker 0.06% 보수적 가정\n"
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
    cached_observations, cached_alert_state = load_snapshots(
        SAMPLE_STORE_PATH, SYMBOL, POLL)
    observations = deque(cached_observations, maxlen=360)
    LOG.info("price_sample_cache_restored=%d", len(observations))
    risk_tracker = SignalRiskTracker()
    position_ledger = PositionState()
    position_bot = TelegramBot(position_ledger)
    LOG.info("position_report_tracking=enabled; recorded_position_present=%s", bool(position_ledger.position))
    LOG.info("risk_snapshot_poll=%ds, entry_snapshot_poll=%ds (sampled data only)", RISK_POLL, POLL)
    watch = None
    alert_state = cached_alert_state
    last_sample = observations[-1][0] if observations else 0
    startup_done = False
    next_startup_test = 0
    counters = {"watched": 0, "confirmed": 0, "expired": 0, "blocked": 0,
                "suppressed": 0, "range_watch": 0, "trend_watch": 0,
                "breakout_watch": 0, "range_confirmed": 0,
                "trend_confirmed": 0, "breakout_confirmed": 0}
    next_log_summary = time.time() + 3600
    last_5m_audit = None
    while True:
        try:
            # Inbound updates use the same authorized existing Telegram chat.
            position_bot.poll()
            price, change24 = get_perpetual_snapshot(exchange)
            now = time.time()
            if STARTUP_TEST_ENABLED and not startup_done and now >= next_startup_test:
                startup_done = telegram("[LBank BTCUSDT] 2단계 감시 재시작. 시험 알림이며 진입 신호 아님.")
                LOG.info("Telegram startup test %s", "SENT" if startup_done else "FAILED")
                next_startup_test = now + STARTUP_RETRY
            # Self-reported *executed* position has priority over hypothetical alerts.
            observed_prices = [v for _, v in observations][-5:] + [price]
            registered_risk = position_ledger.risk(observed_prices)
            if registered_risk is not None:
                reason, observed_price = registered_risk
                p = position_ledger.position
                side = "롱" if p["side"] == "LONG" else "숏"
                kst_at = datetime.fromtimestamp(
                    now, tz=timezone(timedelta(hours=9))).strftime("%Y-%m-%d %H:%M")
                warning = (
                    f"[LBank BTCUSDT | 긴급 위험 경고 - 신고 {side} 포지션]\\n"
                    f"한국시간: {kst_at}\\n"
                    f"위험 사유: {reason}\\n"
                    f"현재 관측가: {observed_price:,.1f} USDT\\n"
                    f"신고 손절가: {float(p['stop']):,.1f} USDT\\n"
                    f"신고 잔여 수량: {p['quantity']} BTC\\n"
                    "실제 보유 중이라면 지금 LBank 손절·포지션 상태 확인 및 "
                    "시장가 청산을 포함한 위험 축소 검토 필요.\\n"
                    "이 메시지는 서버에 신고된 상태 기준이며 실제 주문을 실행하지 않습니다."
                ).replace("\\n", "\n")
                if position_bot.send(warning):
                    position_ledger.acknowledge_risk(reason)
                    LOG.warning("MANUAL POSITION RISK SENT side=%s reason=%s", side, reason)
                else:
                    LOG.error("MANUAL POSITION RISK TELEGRAM FAILURE")
            # Urgent risk messages bypass entry-alert cooldowns and daily quotas.
            # Only already-SENT hypothetical signals are monitored, not real positions.
            for tracked, reason, observed_price in (
                [] if position_ledger.position else risk_tracker.check(observed_prices, now)
            ):
                observed_kst = datetime.fromtimestamp(
                    now, tz=timezone(timedelta(hours=9))).strftime("%Y-%m-%d %H:%M")
                warning = format_risk_alert(
                    tracked, reason, observed_price, observed_kst)
                if telegram(warning):
                    risk_tracker.acknowledge(tracked)
                    LOG.warning(
                        "SIGNAL RISK WARN SENT reference=%d side=%s reason=%s price=%.1f",
                        tracked.number, "LONG" if tracked.direction == 1 else "SHORT",
                        reason, observed_price)
                else:
                    LOG.error("SIGNAL RISK WARN SEND FAILED reference=%d", tracked.number)
            if now - last_sample >= POLL:
                last_sample = now
                observations.append((now, price))
                save_snapshots(SAMPLE_STORE_PATH, SYMBOL, POLL,
                               observations, alert_state)
                # Shadow audit runs for every newly completed 5-minute sample.
                # Capture both rejected and validated observations for 7-day
                # forward testing; never create an order or extra entry alert.
                bucket = int(now // 300) - 1
                if bucket != last_5m_audit:
                    last_5m_audit = bucket
                    audit_state = sampled_five_minute_rsi(list(observations))
                    if audit_state is not None:
                        ready, reason, value = confirmed_sampled_5m_oversold_rebound(
                            list(observations))
                        LOG.info(
                            "RSI5_SHADOW completed_bucket=%d close=%.1f rsi=%.2f "
                            "rebound_ready=%s reason=%s source=sampled_perpetual_ticker",
                            bucket, audit_state["closes"][-1],
                            value, ready, reason)
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
                        plan = confirm_watch(prices, watch["plan"], age, observations=list(observations))
                        if plan is not None:
                            side = plan["side"]
                            if alert_gate(now, side, alert_state) == "ok":
                                message = format_entry_alert(
                                    plan, datetime.fromtimestamp(now, tz=timezone.utc).isoformat())
                                if telegram(message):
                                    record_alert(now, side, alert_state)
                                    save_snapshots(SAMPLE_STORE_PATH, SYMBOL, POLL,
                                                   observations, alert_state)
                                    risk_tracker.add_sent_signal(plan, now)
                                    counters["confirmed"] += 1
                                    source = plan.get("strategy", "breakout")
                                    counters[{"range_rebound": "range_confirmed",
                                              "trend_retest": "trend_confirmed",
                                              "breakout": "breakout_confirmed"}[source]] += 1
                                    LOG.info("ENTRY REVIEW SENT strategy=%s side=%s entry=%.1f stop=%.1f t1=%.1f net_rr=%.2f",
                                             source, side, plan["price"], plan["stop"], plan["target1"], plan["rr1"])
                                else:
                                    LOG.warning("ENTRY REVIEW SEND FAILED")
                            else:
                                counters["suppressed"] += 1
                                LOG.info("ENTRY REVIEW suppressed by cooldown side=%s", side)
                            watch = None
                if continuous and watch is None:
                    proposal = classify(prices, observations=list(observations))
                    if proposal is not None:
                        side = proposal["side"]
                        if alert_gate(now, side, alert_state) == "ok":
                            watch = {"plan": proposal, "time": now}
                            counters["watched"] += 1
                            source = proposal.get("strategy", "breakout")
                            counters[{"range_rebound": "range_watch",
                                      "trend_retest": "trend_watch",
                                      "breakout": "breakout_watch"}[source]] += 1
                            LOG.info("watch created %s strategy=%s at %.1f (no preliminary Telegram noise)",
                                     side, source, proposal["price"])
                        else:
                            counters["suppressed"] += 1
                    else:
                        counters["blocked"] += 1
                LOG.info("contract=BTCUSDT price=%.1f count=%d watch=%s confirmed_total=%d tracked_references=%d",
                         price, len(prices), watch["plan"]["side"] if watch else "none",
                         counters["confirmed"], len(risk_tracker.signals))
                if now >= next_log_summary:
                    LOG.info("ALERT FUNNEL last-hour totals: %s", counters)
                    counters = {key: 0 for key in counters}
                    next_log_summary = now + 3600
        except Exception as exc:
            LOG.error("LBank market error (%s): %s", type(exc).__name__, str(exc)[:160])
        time.sleep(RISK_POLL)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    run()
