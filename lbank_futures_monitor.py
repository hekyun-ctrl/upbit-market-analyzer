"""LBank BTCUSDT perpetual-only public price observation monitor.

Never creates orders or reads private LBank accounts. CCXT routes fetch_ohlcv
to SPOT. Live entries use native closed 1m candle closes with a separate current entry quote.
5m RSI and 15m context use complete native candles; no ticker-history fallback.
Ticker observations remain for fast risk monitoring and hypothetical accounting.
"""
import json
import logging
import math
import os
import threading
import time
import urllib.parse
import urllib.request
from collections import deque
from datetime import datetime, timezone, timedelta

import ccxt
from lbank_signal_risk import SignalRiskTracker, format_risk_alert, format_target_alert, format_target2_alert
from lbank_telegram_positions import PositionState, TelegramBot, format_position_target_alert
from lbank_sample_store import load_snapshots, save_snapshots
from lbank_structure_patterns import detect_structure, detect_structures, confirm_structure
from lbank_sampled_context import sampled_context_gate
from lbank_rsi_research import ResearchTracker, NeutralExitStudy, neutral_stall
from lbank_rsi_plateau import consolidation_reversal_resume
from lbank_macro_monitor import MacroMonitor
from lbank_orderbook_shadow import OrderBookShadowThread
from lbank_notification_schedule import is_quiet_new_entry_time, quiet_hours_enabled
from lbank_compact_alert import format_compact_entry_alert
from lbank_telegram_transport import TelegramTransport
from lbank_performance import PerformanceBook
from lbank_native_candles import NativeCandleThread
from lbank_native_entry import NativeEntryFeed, native_context_gate, SOURCE as NATIVE_SOURCE
from lbank_trend_runner import format_runner_alert, POLICY_VERSION, atr_wilder
from lbank_rsi_divergence import annotate as annotate_rsi_context, DivergenceStudy

LOG = logging.getLogger("lbank_monitor")
TELEGRAM_DELIVERY = TelegramTransport()
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
RUNNER_ENABLED = os.getenv("LBANK_TREND_RUNNER_ENABLED", "true").lower() == "true"
DAILY_GOAL_PCT = 7.0  # Reporting objective only; never an entry/size/exit trigger.

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
LEVERAGE = float(os.getenv("LBANK_REFERENCE_LEVERAGE", "30"))
MAX_MARGIN_FRACTION = float(os.getenv("LBANK_MAX_MARGIN_PCT", "15")) / 100
MIN_NET_RR = 1.5
MIN_SAMPLES = 60
SAMPLE_STORE_PATH = os.getenv("LBANK_SAMPLE_STORE_PATH", "/data/lbank_price_samples.json")
if not (0 < REF_BALANCE <= 1000000 and 0 < RISK_FRACTION <= 0.01 and
        1 <= LEVERAGE <= 50 and 0 < MAX_MARGIN_FRACTION <= 0.15):
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


def confirmed_sampled_5m_plateau_resume(observations, direction):
    """Alternative: 1st completed RSI turn, sideways pause, later confirmed breakout."""
    state = sampled_five_minute_rsi(observations)
    if state is None:
        return False, "5m_plateau_data_unavailable", None
    ok, reason = consolidation_reversal_resume(
        state["closes"], state["rsi"], direction)
    return ok, reason, state["rsi"][-1]


def extreme_5m_rsi_entry_gate(observations, direction):
    """LEGACY SHADOW RESEARCH: extreme reversal paths (NOT a live entry gate).

    A) two consecutive higher/lower completed RSI + sampled price closes, OR
    B) one first turn, 1-12 closed plateau buckets, then RSI + close breakout.
    Sideways alone is never an entry. Ticker samples, NOT genuine 5m OHLCV.
    """
    if direction == 1:
        direct = confirmed_sampled_5m_oversold_rebound(observations)
    elif direction == -1:
        direct = confirmed_sampled_5m_overbought_rejection(observations)
    else:
        return False, "invalid_direction", None
    if direct[0]:
        return direct
    delayed = confirmed_sampled_5m_plateau_resume(observations, direction)
    return delayed if delayed[0] else direct


def five_minute_rsi_entry_gate(observations, direction, setup=None, native_state=None):
    """Direction only; native closed 5m RSI in live mode, legacy samples in replay.

    No absolute RSI threshold or extreme-history veto is imposed.
    """
    if direction not in (1, -1):
        return False, "invalid_direction", None
    state = (native_state["rsi5"] if native_state is not None
             else sampled_five_minute_rsi(observations))
    if state is None or len(state["rsi"]) < 2:
        return False, "5m_rsi_direction_data_unavailable", None
    previous, current = state["rsi"][-2:]
    if direction * (current - previous) <= 0:
        return False, "5m_rsi_direction_mismatch_or_flat", current
    return True, "5m_rsi_direction_aligned", current


def fast_sample_rsi_gate(prices, direction):
    """Legacy research diagnostic ONLY: no longer called by live entry gates.

    Absolute fast-RSI 35/70 thresholds must not block a live LONG/SHORT.
    Only completed ticker-sampled 5-minute RSI *direction* is checked live.
    """
    strength = rsi(prices)
    if direction == -1 and strength <= 35:
        return False, "fast_rsi_short_oversold", strength
    if direction == 1 and strength >= 70:
        return False, "fast_rsi_long_overbought", strength
    return True, "fast_rsi_ok", strength


def telegram(message):
    """Common, audited Telegram delivery; avoids logging credentials/messages.

    Caller-side acknowledgement/retry remains authoritative: ambiguous timeouts
    are never immediately resent as potentially duplicate entry alerts.
    """
    return TELEGRAM_DELIVERY.send(message)


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


def native_entry_geometry(native_state):
    """Volatility-scaled lower stop bound, not a substitute for swing anchors.

Use 1.5 completed 5m ATR, with a 0.05% noise floor. The actual stop remains
beyond the structural extreme plus its existing buffer, and never exceeds
the existing 0.60% distance/risk/margin caps. No missing-data fallback.
The range lower width is the necessary cost-aware minimum TP1 distance,
not the old unconditional 0.60%. Real remaining room is checked in the plan.
"""
    try:
        curr = float(native_state["quote"])
        atr = atr_wilder(native_state["five_bars"])
        if not (math.isfinite(curr) and curr > 0 and atr is not None
                and math.isfinite(atr) and atr > 0):
            return None
        floor = max(.0005, 1.5 * atr / curr)
        targets = []
        for direction in (1, -1):
            loss = -net_pnl_per_btc(direction, curr, curr*(1-direction*floor))
            target = minimum_net_rr_target(direction, curr, loss, MIN_NET_RR)
            if target is None:
                return None
            targets.append(abs(target-curr)/curr)
        return {"atr5": atr, "stop_floor_fraction": floor,
                "minimum_target_fraction": max(targets),
                "min_range_fraction": max(targets)+.00015}
    except (KeyError, TypeError, ValueError, OverflowError):
        return None



def trend_retest_momentum_gate(prices, direction):
    """Reject a flat/weak local retest in *both* directional horizons.

    Only strategy='trend_retest' uses this gate, at both initial watch and
    second-stage confirmation. Thresholds are on SAMPLE-PRICE momentum,
    NOT RSI values, true 5m candles or exchange volume. Both horizons must
    be weak to reject; a strong 5m impulse OR 15m follow-through permits
    standard structural/cost/risk checks to continue.
    """
    if direction not in (-1, 1) or len(prices) < 16 or prices[-1] <= 0:
        return False, "invalid_retest_momentum_data", 0., 0.
    curr = float(prices[-1])
    if min(prices[-6], prices[-16]) <= 0:
        return False, "invalid_retest_momentum_data", 0., 0.
    momentum5 = direction * (curr / prices[-6] - 1) * 100
    momentum15 = direction * (curr / prices[-16] - 1) * 100
    # Tiny floating-point rounding at exactly 0.10%/0.08% is not a veto.
    if momentum5 < .10 - 1e-10 and momentum15 < .08 - 1e-10:
        return False, "weak_retest_momentum_both_horizons", momentum5, momentum15
    return True, "retest_momentum_confirmed", momentum5, momentum15


def calculate_plan(prices, direction, setup=None, native_state=None):
    """Cost-aware hypothetical levels with optional structure-anchored stop."""
    setup = setup or {"strategy": "breakout"}
    strategy = setup.get("strategy", "breakout")
    if len(prices) < MIN_SAMPLES:
        return None
    curr = native_state["quote"] if native_state is not None else prices[-1]
    geometry = native_entry_geometry(native_state) if native_state is not None else None
    if native_state is not None and geometry is None:
        return None
    momentum5 = (curr / prices[-6] - 1) * 100
    momentum15 = (curr / prices[-16] - 1) * 100
    strength = rsi(prices)
    if strategy == "breakout":
        local = prices[-9:-1]
        if native_state is not None:
            key = "low" if direction == 1 else "high"
            local = [bar[key] for bar in native_state["minute_bars"][-8:]]
        minimum_stop = geometry["stop_floor_fraction"] if geometry else .0025
        stop = (min(curr * (1 - minimum_stop), min(local) * (1 - .0003))
                if direction == 1 else
                max(curr * (1 + minimum_stop), max(local) * (1 + .0003)))
    else:
        anchor = setup["structure_anchor"]
        minimum_stop = geometry["stop_floor_fraction"] if geometry else .0012
        stop = (min(curr * (1 - minimum_stop), anchor - curr * .0002)
                if direction == 1 else
                max(curr * (1 + minimum_stop), anchor + curr * .0002))
    stop_distance = abs(curr - stop) / curr
    if not (minimum_stop - 1e-10 <= stop_distance <= .0060):
        LOG.info("PLAN REJECT reason=stop_distance strategy=%s direction=%s price=%.1f stop=%.1f dist_pct=%.3f min_pct=%.3f max_pct=0.600",
                 strategy, direction, curr, stop, stop_distance * 100, minimum_stop * 100)
        return None
    unit_loss = -net_pnl_per_btc(direction, curr, stop)
    if unit_loss <= 0:
        return None
    t1 = minimum_net_rr_target(direction, curr, unit_loss, MIN_NET_RR)
    t2 = minimum_net_rr_target(direction, curr, unit_loss, 2.5)
    if t1 is None or t2 is None or t1 <= 0 or t2 <= 0:
        return None
    # Required profit must be plausible against the observed native candle range
    # otherwise skip instead of inventing a reachable price target.
    observed_range = max(prices[-60:]) - min(prices[-60:])
    if native_state is not None:
        bars = native_state["minute_bars"][-60:]
        observed_range = max(b["high"] for b in bars) - min(b["low"] for b in bars)
    if abs(t1 - curr) > 1.5 * observed_range:
        LOG.info("PLAN REJECT reason=target_outside_observed_range strategy=%s direction=%s price=%.1f target1=%.1f observed_range=%.1f",
                 strategy, direction, curr, t1, observed_range)
        return None
    barrier = setup.get("barrier")
    if barrier is not None and direction * (barrier - t1) < curr * .00015:
        LOG.info("PLAN REJECT reason=target_barrier_conflict strategy=%s direction=%s price=%.1f target1=%.1f barrier=%.1f",
                 strategy, direction, curr, t1, barrier)
        return None
    permitted_loss = REF_BALANCE * RISK_FRACTION
    effective_entry = curr
    raw_qty = min(permitted_loss / unit_loss,
                  REF_BALANCE * MAX_MARGIN_FRACTION * LEVERAGE / effective_entry)
    # Displayed entry margin is a conservative cent-rounded amount in USDT.
    # Rounding DOWN avoids asking the user to deposit more than the modeled cap.
    # This is still only an estimate: live venue contract steps are unverified.
    margin = math.floor(raw_qty * effective_entry / LEVERAGE * 100) / 100
    qty = margin * LEVERAGE / effective_entry
    if qty <= 0:
        return None
    net_loss = -qty * net_pnl_per_btc(direction, curr, stop)
    net_gain1 = qty * net_pnl_per_btc(direction, curr, t1, exit_kind="take_profit")
    net_gain2 = qty * net_pnl_per_btc(direction, curr, t2, exit_kind="take_profit")
    if net_loss <= 0 or net_gain1 / net_loss < MIN_NET_RR - 1e-7:
        return None
    # Alert risk fields always use the same reference balance, fee model and
    # position size that generated the displayed fee-inclusive loss.
    observed_abs_moves = sorted(abs(prices[i] / prices[i-1] - 1) * 100
                                for i in range(max(1, len(prices) - 59), len(prices)))
    one_min_p90 = observed_abs_moves[int(.90 * (len(observed_abs_moves) - 1))]
    return {
        "direction": direction, "side": "롱" if direction == 1 else "숏",
        "strategy": strategy, "level": setup.get("level"),
        "structure_anchor": setup.get("structure_anchor"),
        "barrier": setup.get("barrier"),
        "price": curr, "rsi": strength, "m5": momentum5, "m15": momentum15,
        "stop": stop, "target1": t1, "target2": t2,
        "margin": margin, "notional": qty * curr,
        "quantity_btc": qty, "reference_risk_budget": permitted_loss,
        "stop_distance_pct": stop_distance * 100,
        "sampled_1m_abs_move_p90_pct": one_min_p90,  # Legacy compatibility key.
        "one_minute_close_abs_move_p90_pct": one_min_p90,
        "net_loss": net_loss, "net_gain1": net_gain1, "net_gain2": net_gain2,
        "rr1": net_gain1 / net_loss, "rr2": net_gain2 / net_loss,
        "fee_roundtrip_est": qty * (effective_entry * TAKER_FEE + t1 * TAKER_FEE),
        "fee_stop_est": qty * (effective_entry * TAKER_FEE +
            stop * (1 - direction * STOP_SLIPPAGE) * TAKER_FEE),
        "maker_potential_savings": qty * (effective_entry + t1) * (TAKER_FEE - MAKER_FEE),
        "runner_enabled": bool(native_state is not None and RUNNER_ENABLED),
        "native_entry_geometry": geometry,
    }


def classify(prices, observations=None, diagnostics=None, native_state=None):
    """Evaluate strategies independently; rejected candidates cannot veto others.

    Existing structure priority is retained among same-direction qualified plans.
    Opposite qualified directions abstain instead of ranking by an invented score.
    The complete decision list is retained for opportunity-loss audits.
    """
    if len(prices) < MIN_SAMPLES:
        if diagnostics is not None:
            diagnostics.update(reason="insufficient_samples", candidates=[])
        return None
    if native_state is not None:
        if abs(native_state["quote"] / prices[-1] - 1) > ENTRY_MAX_CHASE_PCT:
            if diagnostics is not None:
                diagnostics.update(reason="price_deviation", candidates=[])
            return None
        bars = native_state["minute_bars"]
        geometry = native_entry_geometry(native_state)
        if geometry is None:
            if diagnostics is not None:
                diagnostics.update(reason="native_volatility_unavailable", candidates=[])
            return None
        setup_diag = {}
        setups = detect_structures(prices, ema, rsi, bars=bars,
                                   min_range_fraction=geometry["min_range_fraction"],
                                   diagnostics=setup_diag)
        setup_diag.update(atr5=geometry["atr5"],
                          minimum_stop_pct=geometry["stop_floor_fraction"]*100,
                          minimum_target_pct=geometry["minimum_target_fraction"]*100,
                          range60_pct=(max(b["high"] for b in bars[-60:])-
                                       min(b["low"] for b in bars[-60:]))/native_state["quote"]*100)
        if diagnostics is not None:
            diagnostics["market"] = setup_diag
    else:
        first = detect_structure(prices, ema, rsi)
        setups = ([first] if first is not None else []) + detect_structures(prices, ema, rsi)
    curr = prices[-1]
    m5, m15 = (curr / prices[-6] - 1) * 100, (curr / prices[-16] - 1) * 100
    macd_arr = [a - b for a, b in zip(ema(prices, 12), ema(prices, 26))]
    macd, sig = macd_arr[-1], ema(macd_arr, 9)[-1]
    prior_high = (max(b["high"] for b in native_state["minute_bars"][-16:-1])
                  if native_state is not None else max(prices[-16:-1]))
    prior_low = (min(b["low"] for b in native_state["minute_bars"][-16:-1])
                 if native_state is not None else min(prices[-16:-1]))
    if native_state is not None and diagnostics is not None:
        diagnostics["market"].update(
            momentum5_pct=m5, momentum15_pct=m15,
            breakout_long=curr>prior_high and macd>sig and m5>=.14 and m15>=.20,
            breakout_short=curr<prior_low and macd<sig and m5<=-.14 and m15<=-.20)
    if curr > prior_high and macd > sig and m5 >= .14 and m15 >= .20:
        setups.append({"direction": 1, "strategy": "breakout"})
    if curr < prior_low and macd < sig and m5 <= -.14 and m15 <= -.20:
        setups.append({"direction": -1, "strategy": "breakout"})
    seen, decisions, qualified = set(), [], []
    for setup in setups:
        key = setup["strategy"], setup["direction"]
        if key in seen:
            continue
        seen.add(key)
        detail = {}
        plan = assess_candidate(prices, setup["direction"], setup,
                                observations=observations, diagnostics=detail,
                                native_state=native_state)
        decisions.append({"strategy": key[0], "direction": key[1],
                          "reason": detail.get("reason", "unknown")})
        if plan is not None:
            qualified.append(plan)
    conflict = len({p["direction"] for p in qualified}) > 1
    reason = ("strategy_conflict" if conflict else "qualified" if qualified else
              decisions[0]["reason"] if decisions else "market_no_setup")
    if diagnostics is not None:
        diagnostics.update(reason=reason, candidates=decisions)
    return qualified[0] if qualified and not conflict else None


def assess_candidate(prices, direction, setup, observations=None, diagnostics=None, native_state=None):
    """Apply the SAME price, cost, context and direction gates per strategy."""
    def mark(reason):
        if diagnostics is not None:
            diagnostics["reason"] = reason
    curr = native_state["quote"] if native_state is not None else prices[-1]
    strength = rsi(prices)
    LOG.info("PREWATCH candidate strategy=%s direction=%s price=%.1f rsi1=%.1f",
             (setup or {}).get("strategy", "breakout"),
             "LONG" if direction == 1 else "SHORT", curr, strength)
    if setup is not None and setup.get("strategy") == "trend_retest":
        momentum_ok, momentum_reason, directional5, directional15 = (
            trend_retest_momentum_gate(prices, direction))
        if not momentum_ok:
            LOG.info("RETEST MOMENTUM WATCH BLOCKED side=%s reason=%s directed5m=%.3f%% directed15m=%.3f%%",
                     "LONG" if direction == 1 else "SHORT",
                     momentum_reason, directional5, directional15)
            mark("retest_momentum")
            return None
    context = None
    if native_state is not None or observations is not None:
        context_ok, context_reason, context = (
            native_context_gate(native_state, direction, (setup or {}).get("strategy", "breakout"))
            if native_state is not None else sampled_context_gate(
                observations, direction, (setup or {}).get("strategy", "breakout")))
        if not context_ok:
            LOG.info("CLOSED-BUCKET watch BLOCKED side=%s reason=%s context=%s",
                     "LONG" if direction == 1 else "SHORT", context_reason, context)
            mark("sampled_context")
            return None
        allowed, reason, sampled_rsi5 = five_minute_rsi_entry_gate(
            observations, direction, setup=setup, native_state=native_state)
        if not allowed:
            LOG.info("5m RSI watch direction BLOCKED side=%s reason=%s rsi=%s",
                     "LONG" if direction == 1 else "SHORT", reason, sampled_rsi5)
            mark("rsi_direction")
            return None
    else:
        sampled_rsi5 = None
    plan = calculate_plan(prices, direction, setup=setup, native_state=native_state)
    if plan is None:
        mark("risk_or_target")
        LOG.info("PREWATCH candidate cost/risk/target rejected strategy=%s direction=%s",
                 (setup or {}).get("strategy", "breakout"), direction)
    if plan is not None:
        mark("qualified")
        if native_state is not None:
            plan["data_source"] = native_state["source"]
            plan["native_5m_start"] = native_state["last5_start"]
            plan["native_5m_count"] = len(native_state["five_bars"])
            plan["rsi5"] = sampled_rsi5
        plan["sampled_rsi5"] = sampled_rsi5  # Legacy schema alias; see data_source.
        plan["sampled_context"] = context
        plan["rsi_direction_status"] = reason if native_state is not None or observations is not None else None
        plan = annotate_rsi_context(plan, native_state)
    return plan


def confirm_watch(prices, watch, age_seconds, observations=None, diagnostics=None, native_state=None):
    """Separate second-stage ENTRY REVIEW with fresh price confirmation.

    Used for user-side manual checking only, not a guaranteed market entry.
    Native mode verifies candle continuity; orderbook depth remains unverified.
    """
    def mark(reason):
        if diagnostics is not None:
            diagnostics["reason"] = reason

    if watch is None or age_seconds < 30 or age_seconds > ENTRY_CONFIRM_MAX_AGE_SECONDS:
        mark("invalid_watch_age")
        return None
    if len(prices) < MIN_SAMPLES:
        mark("insufficient_samples")
        return None
    direction = watch["direction"]
    current = native_state["quote"] if native_state is not None else prices[-1]
    initial = watch["price"]
    relative = direction * (current / initial - 1)
    # Do not chase >0.10% in the favorable direction or accept >0.10% failed breakout.
    if relative < -ENTRY_MAX_CHASE_PCT or relative > ENTRY_MAX_CHASE_PCT:
        mark("price_deviation")
        return None
    m5 = (current / prices[-6] - 1) * 100
    m15 = (current / prices[-16] - 1) * 100
    macd_line = [a - b for a, b in zip(ema(prices, 12), ema(prices, 26))]
    macd, sig = macd_line[-1], ema(macd_line, 9)[-1]
    strength = rsi(prices)
    if direction == 1:
        valid = macd > sig and m5 >= .04 and m15 >= .10
    else:
        valid = macd < sig and m5 <= -.04 and m15 <= -.10
    if watch.get("strategy", "breakout") != "breakout":
        valid = (confirm_structure(prices, watch, ema, rsi, current_price=current)
                 if native_state is not None else confirm_structure(prices, watch, ema, rsi))
    if not valid:
        mark("structure_or_momentum")
        return None
    if watch.get("strategy") == "trend_retest":
        momentum_ok, momentum_reason, directional5, directional15 = (
            trend_retest_momentum_gate(prices, direction))
        if not momentum_ok:
            LOG.info("RETEST MOMENTUM CONFIRM BLOCKED side=%s reason=%s directed5m=%.3f%% directed15m=%.3f%%",
                     "LONG" if direction == 1 else "SHORT",
                     momentum_reason, directional5, directional15)
            mark("retest_momentum")
            return None
    context = None
    if native_state is not None or observations is not None:
        context_ok, context_reason, context = (
            native_context_gate(native_state, direction, watch.get("strategy", "breakout"))
            if native_state is not None else sampled_context_gate(
                observations, direction, watch.get("strategy", "breakout")))
        if not context_ok:
            LOG.info("CLOSED-BUCKET confirm BLOCKED side=%s reason=%s context=%s",
                     "LONG" if direction == 1 else "SHORT", context_reason, context)
            mark("sampled_context")
            return None
        allowed, reason, sampled_rsi5 = five_minute_rsi_entry_gate(
            observations, direction, setup=watch, native_state=native_state)
        if not allowed:
            LOG.info("5m RSI confirm direction BLOCKED side=%s reason=%s rsi=%s",
                     "LONG" if direction == 1 else "SHORT", reason, sampled_rsi5)
            mark("rsi_direction")
            return None
    else:
        sampled_rsi5 = None
    plan = calculate_plan(prices, direction, setup=watch, native_state=native_state)
    if plan is None:
        mark("risk_or_target")
        LOG.info("CONFIRM candidate cost/risk/target rejected strategy=%s direction=%s",
                 watch.get("strategy", "breakout"), direction)
    if plan is not None:
        mark("qualified")
        if native_state is not None:
            plan["data_source"] = native_state["source"]
            plan["native_5m_start"] = native_state["last5_start"]
            plan["native_5m_count"] = len(native_state["five_bars"])
            plan["rsi5"] = sampled_rsi5
        plan["sampled_rsi5"] = sampled_rsi5  # Legacy schema alias; see data_source.
        plan["sampled_context"] = context
        plan["rsi_direction_status"] = reason if native_state is not None or observations is not None else None
        plan = annotate_rsi_context(plan, native_state)
    return plan


def format_entry_alert(plan, observed_at_utc, reference_number=None):
    """Send only the actionable 9-line, USDT-first, KST observation summary.

    Full market diagnostics and filter decisions remain in Railway logs.
    Manual entries must verify live venue price, contract size and fees.
    """
    return format_compact_entry_alert(
        plan, observed_at_utc, reference_number=reference_number,
        reference_balance=REF_BALANCE, leverage=LEVERAGE,
        max_margin_fraction=MAX_MARGIN_FRACTION,
    )


def verified_reversal_exception(now, side, state, plan=None):
    """Permit early opposite-side review ONLY on a strict closed-bucket reversal.

    The global 45m cooldown, daily cap, two-stage confirmation, no-chase and
    net cost/risk checks are NOT waived. Directional RSI is secondary evidence.
    """
    previous_at = state.get("last_time")
    if previous_at is None or state.get("last_side") == side:
        return False
    elapsed = now - previous_at
    if not (COOLDOWN <= elapsed < REVERSAL_COOLDOWN):
        return False
    if not isinstance(plan, dict):
        return False
    if plan.get("side") != side or plan.get("strategy") not in ("breakout", "trend_retest"):
        return False
    context = plan.get("sampled_context") or {}
    try:
        direction = 1 if side == "롱" else -1
        entry, stop, target = (float(plan[key]) for key in
                               ("price", "stop", "target1"))
        rr, loss = float(plan["rr1"]), float(plan["net_loss"])
        risk_valid = (entry > 0 and stop > 0 and target > 0 and
                      direction * (entry - stop) > 0 and
                      direction * (target - entry) > 0 and
                      0 < loss <= REF_BALANCE * RISK_FRACTION + 1e-7 and
                      rr >= MIN_NET_RR - 1e-7)
    except (KeyError, TypeError, ValueError, OverflowError):
        return False
    return (risk_valid and context.get("reversal_confirmed") is True and
            plan.get("rsi_direction_status") == "5m_rsi_direction_aligned")


def format_reversal_pre_watch(plan, at_kst):
    """Information-only verified reversal watch, never an entry permission."""
    context = plan.get("sampled_context") or {}
    return (
        f"[LBank BTCUSDT | {plan['side']} 전환 사전포착 - 진입 미확정]\n"
        f"한국시간: {at_kst}\n"
        f"현재 참고가격: {plan['price']:,.1f} USDT\n"
        f"완료된 5분 가격표본 2구간 회복: {context.get('reversal_10m_pct', 0):+.2f}%\n"
        f"완료된 최신 5분 구간 움직임: {context.get('reversal_last5_pct', 0):+.2f}%\n"
        "기존 반대 방향 알람의 90분 제한 중 검증된 전환 후보를 포착했습니다.\n"
        "※ 2단계 재확인 전이므로 진입 추천이나 확정 알람이 아닙니다.\n"
        "※ 수수료 포함 위험·손절·목표의 추가 검증 및 거래소 실제 5분봉/호가 확인 필요.\n"
        "※ 거래소 원본 OHLCV/거래량이 아닌 1분 선물 가격표본 기반입니다. 자동주문 없음."
    )


def alert_gate(now, side, state, plan=None):
    day = datetime.fromtimestamp(now, tz=timezone(timedelta(hours=9))).date().isoformat()
    if day != state["day"]:
        state["day"], state["count"] = day, 0
    # Quiet hours apply only to discretionary entry and reversal pre-watch.
    # Sent-signal/actual-position risk and target notices bypass this gate.
    if is_quiet_new_entry_time(now):
        return "sleep_hours"
    if state["count"] >= MAX_DAILY_ALERTS:
        return "daily_cap"
    if state["last_time"] is not None:
        elapsed = now - state["last_time"]
        if elapsed < COOLDOWN:
            return "global_cooldown"
        if side != state["last_side"] and elapsed < REVERSAL_COOLDOWN:
            if not verified_reversal_exception(now, side, state, plan):
                return "reverse_cooldown"
    return "ok"


def record_alert(now, side, state, plan=None):
    if alert_gate(now, side, state, plan=plan) != "ok":
        raise ValueError("alert gate disallowed sending")
    state["last_time"], state["last_side"] = now, side
    state["count"] += 1


def run():
    exchange = ccxt.lbank({"enableRateLimit": True, "timeout": 12000,
                           "options": {"defaultType": "swap"}})
    exchange.load_markets()
    if not exchange.market(SYMBOL).get("swap"):
        raise RuntimeError("Refuse to monitor spot instrument")
    LOG.info("LBank BTCUSDT: TWO-STAGE fee-aware entry-review monitor started. REF_BALANCE=%.4f risk_pct=%.2f leverage=%.0fx max_margin_pct=%.1f; NO orders",
             REF_BALANCE, 100 * RISK_FRACTION, LEVERAGE, MAX_MARGIN_FRACTION * 100)
    LOG.info("ENTRY_NOTIFICATION_SCHEDULE timezone=KST quiet_hours_enabled=%s "
             "entry_hours=%s risk_notifications=always_on",
             quiet_hours_enabled(),
             "quiet=Wed-Sat 23:00-to-next-day-06:00" if quiet_hours_enabled() else "24h")
    # Runs in a daemon so health checks cannot hold up market or stop warnings.
    threading.Thread(target=TELEGRAM_DELIVERY.startup_probe_once,
                     name="lbank-telegram-one-time-diagnostic",
                     daemon=True).start()
    cached_observations, cached_alert_state = load_snapshots(
        SAMPLE_STORE_PATH, SYMBOL, POLL)
    observations = deque(cached_observations, maxlen=360)
    LOG.info("price_sample_cache_restored=%d", len(observations))
    # Only the writer thread owns a writable native DB connection. The entry
    # reader fails closed on missing, stale or discontinuous native history.
    if os.getenv("LBANK_NATIVE_CANDLES_ENABLED", "true").lower() == "true":
        NativeCandleThread().start()
        LOG.info("NATIVE_CANDLES collection enabled; entry_source=native readiness_required=true")
    native_feed = NativeEntryFeed()
    risk_tracker = SignalRiskTracker(path=os.getenv(
        "LBANK_SIGNAL_FOLLOWUP_STATE_PATH", "/data/lbank_signal_followups.json"))
    rsi_research = ResearchTracker()  # Shadow stats only; not an entry gate
    neutral_exit_study = NeutralExitStudy(path=os.getenv(
        "LBANK_NATIVE_NEUTRAL_EXIT_RESEARCH_STATE_PATH", "/data/lbank_native_neutral_exit_study.json"))
    LOG.info("neutral_exit_shadow_study=enabled; logging hypotheses only, no new Telegram exit alerts")
    position_ledger = PositionState()
    position_bot = TelegramBot(position_ledger)
    performance = None
    divergence_study = None
    try:
        divergence_study = DivergenceStudy(os.getenv(
            "LBANK_RSI_DIVERGENCE_STUDY_DB", "/data/lbank_rsi_divergence_study.sqlite3"), REF_BALANCE)
        LOG.info("RSI_DIVERGENCE enabled annotation_only=true independent_paper=true live_gate=false "
                 "pivot_right_bars=2 rsi50_required_for_existing=false")
    except Exception as exc:
        LOG.error("RSI_DIVERGENCE study_unavailable exception=%s existing_entries_unchanged=true", type(exc).__name__)
    pending_journal = []
    try:
        performance = PerformanceBook(
            os.getenv("LBANK_PERFORMANCE_DB", "/data/lbank_performance.sqlite3"),
            REF_BALANCE, goal_pct=DAILY_GOAL_PCT)
        LOG.info("PERFORMANCE_V3 ready daily_goal_pct=7 report_kst=22:50 "
                 "partial_exit=50pct source=ticker_samples actual_account_pnl=false")
        LOG.info("TREND_RUNNER enabled=%s policy=%s atr=14 trail_atr=2 "
                 "ema=9/21 max_tracking_hours=6 goal_is_exit_cap=false validated_profit=false",
                 RUNNER_ENABLED, POLICY_VERSION)
    except Exception as exc:
        LOG.error("PERFORMANCE_V2 unavailable exception=%s; new_entries_paused=true",
                  type(exc).__name__)
    LOG.info("position_report_tracking=enabled; recorded_position_present=%s", bool(position_ledger.position))
    # Official macro feeds are polled in an independent daemon thread.
    # No macro network call can block ticker sampling, stop-risk warning,
    # RSI validation or any other market-monitor action.
    try:
        MacroMonitor(
            sender=telegram,
            state_path=os.getenv("LBANK_MACRO_STATE_PATH", "/data/lbank_macro_state.json"),
        ).start()
        LOG.info("MACRO integration enabled official BLS/Fed news + BLS/BEA schedule; informative only")
    except Exception as exc:
        LOG.error("MACRO integration failed to start exception=%s", type(exc).__name__)
    # Independent read-only LBank futures order-book data collection.
    # Non-fatal in case this public API is inaccessible in this deployment.
    # It NEVER affects the existing entry/safety rules or live Telegram sends.
    if os.getenv("LBANK_ORDERBOOK_SHADOW_ENABLED", "true").lower() == "true":
        try:
            OrderBookShadowThread(
                interval_seconds=int(os.getenv(
                    "LBANK_ORDERBOOK_SHADOW_INTERVAL_SECONDS", "30"))).start()
            LOG.info("ORDERBOOK_SHADOW integration started independent daemon")
        except Exception as exc:
            LOG.error("ORDERBOOK_SHADOW_INIT_FAILED exception=%s details=%s",
                      type(exc).__name__, str(exc)[:150])
    LOG.info("risk_snapshot_poll=%ds, entry_review_poll=%ds source=native_candles quote_risk_polling=true", RISK_POLL, POLL)
    LOG.info("NATIVE_ENTRY_GEOMETRY stop_floor=max(0.05pct,1.5ATR5) "
             "structural_stop_preserved=true range_floor=cost_solved_TP1 "
             "net_rr1=1.5 risk_limit_unchanged=true")
    watch = None
    alert_state = cached_alert_state
    last_sample = observations[-1][0] if observations else 0
    startup_done = False
    next_startup_test = 0
    counter_template = {"watched": 0, "confirmed": 0, "expired": 0, "blocked": 0,
                        "suppressed": 0, "range_watch": 0, "trend_watch": 0,
                        "breakout_watch": 0, "range_confirmed": 0,
                        "trend_confirmed": 0, "breakout_confirmed": 0,
                        "data_gap": 0, "no_setup": 0, "retest_momentum": 0,
                        "sampled_context": 0, "rsi_direction": 0,
                        "risk_or_target": 0, "other_reject": 0,
                        "confirm_reject": 0}
    counters = dict(counter_template)
    diag_counters = dict(counter_template)
    def count_event(label):
        counters[label] += 1
        diag_counters[label] += 1
    next_log_summary = time.time() + 3600
    next_diag_summary = time.time() + 600
    last_5m_audit = None
    last_entry_diagnostics = {}
    while True:
        try:
            # Inbound updates use the same authorized existing Telegram chat.
            position_bot.poll()
            price, change24 = get_perpetual_snapshot(exchange)
            now = time.time()
            native_state, native_diag = native_feed.snapshot(now, price)
            audit_ready = performance is not None
            if performance is not None:
                try:
                    # Retry an acknowledged notification's failed write before
                    # advancing the model or allowing another entry. A process
                    # crash in the delivery/write interval remains unauditable.
                    while pending_journal:
                        method, args, kwargs = pending_journal[0]
                        getattr(performance, method)(*args, **kwargs)
                        pending_journal.pop(0)
                    for outcome in performance.observe(now, price, native_state=native_state):
                        LOG.info("SIGNAL_MODEL_OUTCOME %s", json.dumps(outcome))
                except Exception as exc:
                    audit_ready = False
                    LOG.error("PERFORMANCE_V2 observe_failed exception=%s", type(exc).__name__)
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
            # A self-reported position takes priority over hypothetical signals.
            # Goal notifications are separate from the entry cooldown/daily cap.
            # A sampled price crossing is NOT exchange order execution.
            reported_target_price = position_ledger.target_reached([price])
            if reported_target_price is not None:
                kst_at = datetime.fromtimestamp(
                    now, tz=timezone(timedelta(hours=9))).strftime("%Y-%m-%d %H:%M")
                p = position_ledger.position
                target_message = format_position_target_alert(
                    p, reported_target_price, kst_at)
                if position_bot.send(target_message):
                    position_ledger.acknowledge_target()
                    LOG.info("MANUAL POSITION TARGET NOTICE SENT stage=%d side=%s target=%.1f price=%.1f",
                             p.get("target_stage", 1), p["side"],
                             float(p["target"]), reported_target_price)
                else:
                    LOG.error("MANUAL POSITION TARGET NOTICE DELIVERY FAILED stage=%d",
                              p.get("target_stage", 1))
            # Urgent risk messages bypass entry-alert cooldowns and daily quotas.
            # Only already-SENT hypothetical signals are monitored, not real positions.
            for tracked, reason, observed_price in (
                [] if position_ledger.position else risk_tracker.check(
                    observed_prices, now, native_state=native_state)
            ):
                observed_kst = datetime.fromtimestamp(
                    now, tz=timezone(timedelta(hours=9))).strftime("%Y-%m-%d %H:%M")
                notice = (
                    format_target_alert(tracked, observed_price, observed_kst)
                    if reason == "target1_reached"
                    else format_target2_alert(tracked, observed_price, observed_kst)
                    if reason == "target2_reached"
                    else format_runner_alert(tracked, reason, observed_price, observed_kst)
                    if reason in ("trail_update", "target_extended", "trend_exit", "neutral_exit", "time_exit")
                    else format_risk_alert(tracked, reason, observed_price, observed_kst)
                )
                if telegram(notice):
                    if reason == "early_invalidation" and performance is not None:
                        try:
                            performance.early_warning(tracked.number, tracked.sent_at,
                                                      now, observed_price)
                        except Exception as exc:
                            audit_ready = False
                            pending_journal.append(("early_warning",
                                (tracked.number, tracked.sent_at, now, observed_price), {}))
                            LOG.error("PERFORMANCE_V2 warning_failed exception=%s", type(exc).__name__)
                    if tracked.pending_runner is not None:
                        LOG.info("RUNNER_FOLLOWUP_SENT reference=%d proposal=%s",
                                 tracked.number, json.dumps(tracked.pending_runner, sort_keys=True))
                    risk_tracker.acknowledge(tracked, reason=reason)
                    LOG.info(
                        "SIGNAL FOLLOWUP SENT reference=%d side=%s reason=%s price=%.1f",
                        tracked.number, "LONG" if tracked.direction == 1 else "SHORT",
                        reason, observed_price)
                else:
                    LOG.error("SIGNAL FOLLOWUP SEND FAILED reference=%d reason=%s",
                              tracked.number, reason)
            if now - last_sample >= POLL:
                last_sample = now
                observations.append((now, price))
                save_snapshots(SAMPLE_STORE_PATH, SYMBOL, POLL,
                               observations, alert_state)
                if native_state is not None:
                    native_diag["entry_geometry"] = native_entry_geometry(native_state)
                LOG.info("NATIVE_ENTRY_STATUS %s", json.dumps(native_diag, sort_keys=True))
                # Research and neutral-position warnings also use native 5m RSI.
                # Shadow audit runs for every newly completed 5-minute candle.
                # Capture both rejected and validated observations for 7-day
                # forward testing; never create an order or extra entry alert.
                bucket = native_state["last5_start"] // 300 if native_state else None
                if bucket is not None and bucket != last_5m_audit:
                    last_5m_audit = bucket
                    audit_state = native_state["rsi5"]
                    if audit_state is not None:
                        LOG.info("RSI5_NATIVE completed_bucket=%d close=%.1f rsi=%.2f source=%s",
                                 bucket, audit_state["closes"][-1], audit_state["rsi"][-1], NATIVE_SOURCE)
                        sampled_closes = audit_state["closes"]
                        sampled_rsis = audit_state["rsi"]
                        hypotheses, outcomes = rsi_research.observe(
                            bucket, sampled_closes[-1], sampled_closes, sampled_rsis)
                        neutral_outcomes = neutral_exit_study.observe(
                            bucket, sampled_closes, sampled_rsis)
                        for outcome in neutral_outcomes:
                            LOG.info("NEUTRAL_EXIT_SHADOW %s",
                                     json.dumps(outcome, sort_keys=True))
                        long_stall = neutral_stall(sampled_closes, sampled_rsis, 1)
                        short_stall = neutral_stall(sampled_closes, sampled_rsis, -1)
                        LOG.info("RSI5_STUDY completed_bucket=%d %s",
                                 bucket, json.dumps({
                                     "zones":hypotheses,
                                     "neutral_stall_long":long_stall,
                                     "neutral_stall_short":short_stall,
                                     "close":sampled_closes[-1],
                                     "rsi":round(sampled_rsis[-1],2),
                                     "source":NATIVE_SOURCE,
                                     "orderbook":"unverified"},sort_keys=True))
                        for result in outcomes:
                            LOG.info("RSI5_FORWARD %s",json.dumps(result,sort_keys=True))
                        # A registered MANUAL position may receive one neutral
                        # warning. No report: no position or automatic exit inferred.
                        reported = position_ledger.position
                        if (reported is not None and
                                reported.get("opened_at") is not None and
                                now - float(reported["opened_at"]) >= 600 and
                                not reported.get("neutral_notice", False) and
                                reported.get("risk_notice") is None):
                            side_num = 1 if reported["side"] == "LONG" else -1
                            if neutral_stall(sampled_closes, sampled_rsis, side_num):
                                side_text = "롱" if side_num == 1 else "숏"
                                warning = (
                                    "[LBank BTCUSDT | RSI 5분봉 중립 정체 주의 - "
                                    "수동 신고 포지션]\\n"
                                    f"신고 방향: {side_text}\\n"
                                    f"5분봉 종가: {sampled_closes[-1]:,.1f} USDT\\n"
                                    f"5분봉 RSI: {sampled_rsis[-1]:.1f}\\n"
                                    "2회 완료된 5분 구간에서 RSI 40~60 정체와 "
                                    "가격 모멘텀 약화를 관찰했습니다.\\n"
                                    "원본 봉 기반이며 실제 호가·포지션·체결은 별도 확인이 필요합니다.\\n"
                                    "목표까지 무조건 기다리지 말고 수수료, 실제 손익과 "
                                    "LBank 포지션 상태를 확인해 조기청산을 검토하세요.\\n"
                                    "주의 알림일 뿐 실제 청산 주문은 실행하지 않습니다."
                                ).replace("\\\\n", "\\n")
                                if position_bot.send(warning):
                                    reported["neutral_notice"] = True
                                    position_ledger.save()
                                    LOG.warning("MANUAL POSITION NEUTRAL STALL REVIEW SENT side=%s",side_text)
                                else:
                                    LOG.error("MANUAL POSITION NEUTRAL STALL REVIEW SEND FAILED")
                # Preserve minute-indexed strategy timing with native 1m closes.
                # The live quote is used separately for entry/confirmation prices.
                prices = native_state["prices"] if native_state else []
                # Persist raw reference quotes even when no strategy proposes a
                # trade. A failed journal pauses entries but never risk notices.
                if performance is not None:
                    try:
                        performance.sample(now, price)
                    except Exception as exc:
                        audit_ready = False
                        LOG.error("PERFORMANCE_V2 sample_failed exception=%s", type(exc).__name__)
                continuous = native_state is not None
                if not audit_ready:
                    watch = None
                    LOG.error("ENTRY JOURNAL UNAVAILABLE new_entries_paused=true risk_notices=active")
                elif not continuous:
                    watch = None
                    count_event("data_gap")
                    last_entry_diagnostics = {"reason": "native_data_unavailable", "native": native_diag}
                    if performance is not None:
                        performance.sample(now, price, decision=last_entry_diagnostics)
                elif watch is not None:
                    age = now - watch["time"]
                    if age > ENTRY_CONFIRM_MAX_AGE_SECONDS:
                        expired_plan = watch["plan"]
                        watch = None
                        count_event("expired")
                        LOG.info("watch expired; no ENTRY REVIEW signal side=%s strategy=%s price=%.1f",
                                 expired_plan.get("side"), expired_plan.get("strategy"),
                                 expired_plan.get("price"))
                    else:
                        confirm_diag = {}
                        plan = confirm_watch(prices, watch["plan"], age,
                                             native_state=native_state,
                                             diagnostics=confirm_diag)
                        if plan is None:
                            count_event("confirm_reject")
                            LOG.info("ALERT_CONFIRM_REJECT reason=%s age_seconds=%.1f strategy=%s",
                                     confirm_diag.get("reason", "other"), age,
                                     watch["plan"].get("strategy"))
                        if plan is not None:
                            side = plan["side"]
                            gate_reason = alert_gate(now, side, alert_state, plan=plan)
                            if gate_reason == "ok":
                                # The next reference ID is assigned only after
                                # Telegram delivery succeeds. This same ID is
                                # shown on all later signal followups.
                                # Record fresh stage-two *price progress* separately
                                # from 5m/15m momentum. A structural re-test can
                                # confirm while making almost no fresh progress.
                                proposed_at = float(watch["plan"]["price"])
                                stage_move = (
                                    plan["direction"] * (plan["price"] / proposed_at - 1)
                                    * 100 if proposed_at > 0 else 0.0)
                                plan["confirmation_progress_pct"] = stage_move
                                ctx = plan.get("sampled_context") or {}
                                signed_45m = plan["direction"] * ctx.get(
                                    "trend15_pct", 0.0)
                                LOG.warning(
                                    "ENTRY_EVIDENCE_AUDIT strategy=%s side=%s "
                                    "stage2_signed_progress_pct=%.5f "
                                    "native_1m_to_quote_move5_pct=%.4f "
                                    "native_1m_to_quote_move15_pct=%.4f "
                                    "native_15m_45m_signed_pct=%.4f "
                                    "confirmed_reversal=%s "
                                    "read_only_no_quality_gate_change=true",
                                    plan.get("strategy"), side, stage_move,
                                    plan.get("m5"), plan.get("m15"),
                                    signed_45m, ctx.get("reversal_confirmed"))
                                message = format_entry_alert(
                                    plan, datetime.fromtimestamp(now, tz=timezone.utc).isoformat(),
                                    reference_number=risk_tracker.next_number)
                                if telegram(message):
                                    reverse_exception = verified_reversal_exception(
                                        now, side, alert_state, plan)
                                    record_alert(now, side, alert_state, plan=plan)
                                    save_snapshots(SAMPLE_STORE_PATH, SYMBOL, POLL,
                                                   observations, alert_state)
                                    sent_signal = risk_tracker.add_sent_signal(plan, now)
                                    try:
                                        performance.add(sent_signal.number, plan, now,
                                                        fee=TAKER_FEE, slippage=STOP_SLIPPAGE,
                                                        funding=FUNDING_BUFFER)
                                    except Exception as exc:
                                        audit_ready = False
                                        pending_journal.append(("add", (sent_signal.number, dict(plan), now),
                                            {"fee": TAKER_FEE, "slippage": STOP_SLIPPAGE,
                                             "funding": FUNDING_BUFFER}))
                                        LOG.error("PERFORMANCE_V2 delivered_signal_write_failed exception=%s",
                                                  type(exc).__name__)
                                    count_event("confirmed")
                                    source = plan.get("strategy", "breakout")
                                    count_event({"range_rebound": "range_confirmed",
                                                 "trend_retest": "trend_confirmed",
                                                 "breakout": "breakout_confirmed"}[source])
                                    LOG.info("ENTRY REVIEW SENT strategy=%s side=%s entry=%.1f stop=%.1f t1=%.1f t2=%.1f net_rr=%.2f reverse_exception=%s",
                                             source, side, plan["price"], plan["stop"],
                                             plan["target1"], plan["target2"],
                                             plan["rr1"], reverse_exception)
                                else:
                                    LOG.warning("ENTRY REVIEW SEND FAILED")
                            else:
                                count_event("suppressed")
                                LOG.info("ENTRY REVIEW SUPPRESSED side=%s reason=%s strategy=%s price=%.1f",
                                         side, gate_reason, plan.get("strategy", "breakout"),
                                         plan["price"])
                            watch = None
                if continuous and audit_ready and watch is None:
                    rejection = {}
                    proposal = classify(prices, native_state=native_state,
                                        diagnostics=rejection)
                    rejection["data_source"] = NATIVE_SOURCE
                    last_entry_diagnostics = rejection
                    performance.sample(now, price, decision=rejection)
                    if proposal is not None:
                        side = proposal["side"]
                        gate_reason = alert_gate(now, side, alert_state, plan=proposal)
                        if gate_reason == "ok":
                            watch = {"plan": proposal, "time": now}
                            count_event("watched")
                            source = proposal.get("strategy", "breakout")
                            count_event({"range_rebound": "range_watch",
                                         "trend_retest": "trend_watch",
                                         "breakout": "breakout_watch"}[source])
                            LOG.info("watch created %s strategy=%s at %.1f context=%s",
                                     side, source, proposal["price"],
                                     proposal.get("sampled_context"))
                            if verified_reversal_exception(now, side, alert_state, proposal):
                                LOG.info("REVERSE COOLDOWN EXCEPTION WATCH side=%s strategy=%s price=%.1f",
                                         side, source, proposal["price"])
                                prior_preview = alert_state.get("last_reversal_preview")
                                if prior_preview is None or now - prior_preview >= REVERSAL_COOLDOWN:
                                    note = format_reversal_pre_watch(
                                        proposal, datetime.fromtimestamp(
                                            now, tz=timezone(timedelta(hours=9))).strftime("%Y-%m-%d %H:%M"))
                                    if telegram(note):
                                        alert_state["last_reversal_preview"] = now
                                        save_snapshots(SAMPLE_STORE_PATH, SYMBOL, POLL,
                                                       observations, alert_state)
                                        LOG.info("VERIFIED REVERSE PREWATCH SENT side=%s price=%.1f",
                                                 side, proposal["price"])
                                    else:
                                        LOG.warning("VERIFIED REVERSE PREWATCH DELIVERY FAILED side=%s", side)
                        else:
                            count_event("suppressed")
                            LOG.info("WATCH SUPPRESSED side=%s reason=%s strategy=%s price=%.1f",
                                     side, gate_reason, proposal.get("strategy", "breakout"),
                                     proposal["price"])
                    else:
                        count_event("blocked")
                        bucket = rejection.get("reason", "other_reject")
                        if bucket == "market_no_setup":
                            bucket = "no_setup"
                        elif bucket == "insufficient_samples":
                            bucket = "data_gap"
                        if bucket not in counter_template:
                            bucket = "other_reject"
                        count_event(bucket)
                LOG.info("contract=BTCUSDT price=%.1f count=%d watch=%s confirmed_total=%d tracked_references=%d",
                         price, len(prices), watch["plan"]["side"] if watch else "none",
                         counters["confirmed"], len(risk_tracker.signals))
                if now >= next_log_summary:
                    LOG.info("ALERT FUNNEL last-hour totals: %s", counters)
                    counters = dict(counter_template)
                    next_log_summary = now + 3600
                if now >= next_diag_summary:
                    LOG.info("NATIVE_SETUP_DIAGNOSTICS %s", json.dumps(last_entry_diagnostics, sort_keys=True))
                    LOG.info("ALERT_DIAGNOSTICS_10M no_setup=%d data_gap=%d "
                             "retest_momentum=%d sampled_context=%d "
                             "rsi_direction=%d risk_or_target=%d "
                             "confirm_reject=%d watched=%d confirmed=%d "
                             "suppressed=%d (no_setup != rejected trade)",
                             diag_counters["no_setup"], diag_counters["data_gap"],
                             diag_counters["retest_momentum"],
                             diag_counters["sampled_context"],
                             diag_counters["rsi_direction"],
                             diag_counters["risk_or_target"],
                             diag_counters["confirm_reject"],
                             diag_counters["watched"], diag_counters["confirmed"],
                             diag_counters["suppressed"])
                    diag_counters = dict(counter_template)
                    next_diag_summary = now + 600
                if divergence_study is not None:
                    try:
                        study = divergence_study.step(now,price,native_state,calculate_plan,native_context_gate,
                                  baseline_reason=last_entry_diagnostics.get("reason","not_reviewed"))
                        LOG.info("RSI_DIVERGENCE_STUDY %s",json.dumps(study,sort_keys=True))
                    except Exception as exc:
                        LOG.error("RSI_DIVERGENCE study_failed exception=%s existing_entries_unchanged=true",type(exc).__name__)
                if performance is not None:
                    try:
                        digest = performance.daily_message(now)
                        if digest is not None:
                            if divergence_study is not None:
                                try:
                                    digest += divergence_study.report_line()
                                except Exception as exc:
                                    LOG.error("RSI_DIVERGENCE report_failed exception=%s",type(exc).__name__)
                            delivered = telegram(digest)
                            performance.report_delivery(now, delivered)
                            LOG.info("PERFORMANCE_DAILY_REPORT delivered=%s actual_pnl=false", delivered)
                    except Exception as exc:
                        LOG.error("PERFORMANCE_V2 report_failed exception=%s", type(exc).__name__)
        except Exception as exc:
            LOG.error("LBank market error (%s): %s", type(exc).__name__, str(exc)[:160])
        time.sleep(RISK_POLL)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    run()
