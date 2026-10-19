"""Predeclared native-bar exit policy, not a fitted predictor or order executor.

Wilder ATR(14), EMA(9/21), aligned 15m direction and relative native volume.
Only post-entry, completed bars may move protection/extend a target. Parameters
are hypotheses: no claim of a 70% win rate or a 7% daily return.
"""
import math

from lbank_native_entry import SOURCE
from lbank_rsi_research import neutral_stall

RUNNER_TTL = 6 * 3600
POLICY_VERSION = "native_atr_runner_v1"


def ema_series(values, period):
    result, value = [], values[0]
    for close in values:
        value += 2 / (period + 1) * (close - value)
        result.append(value)
    return result


def atr_wilder(bars, period=14):
    if len(bars) <= period:
        return None
    ranges = [max(b["high"] - b["low"], abs(b["high"] - a["close"]),
                  abs(b["low"] - a["close"])) for a, b in zip(bars, bars[1:])]
    value = sum(ranges[:period]) / period
    for tr in ranges[period:]:
        value = ((period - 1) * value + tr) / period
    return value


def valid_state(state, now):
    """Defence at the policy boundary, including wall-clock freshness."""
    try:
        bars = state["five_bars"]
        if state["source"] != SOURCE or len(bars) < 21:
            return False
        expected = int(now // 300) * 300 - 300
        if bars[-1]["start"] != expected or state["last5_start"] != expected:
            return False
        for i, b in enumerate(bars):
            o, h, l, c, v = [float(b[k]) for k in ("open", "high", "low", "close", "volume")]
            if (not all(math.isfinite(x) for x in (o, h, l, c, v))
                    or min(o, h, l, c) <= 0 or v < 0
                    or h < max(o, c, l) or l > min(o, c, h)
                    or b["quality"] != "stream_closed" or b["start"] % 300
                    or (i and b["start"] - bars[i-1]["start"] != 300)):
                return False
        return (len(state["closes15"]) >= 2 and len(state["rsi5"]["rsi"]) >= 3
                and all(math.isfinite(float(x)) for x in
                        state["closes15"] + state["rsi5"]["rsi"]))
    except (KeyError, TypeError, ValueError, IndexError):
        return False


def runner_decision(*, direction, entry, stop, target2, sent_at, last_bar,
                    state, now, price, strategy="breakout", barrier=None,thesis_evidence=None):
    """Return an advisory proposal; caller commits it only after delivery.

The previous stop must be checked BEFORE calling. No percentage-of-seed TP,
new position, leverage change or averaging down exists in this policy.
"""
    if direction not in (-1, 1) or not valid_state(state, now):
        return None
    bars, d = state["five_bars"], direction
    current_bar = bars[-1]["start"]
    # Do not trail on a bar already handled or one partly preceding entry.
    if current_bar <= last_bar or current_bar < sent_at:
        return None
    closes = [b["close"] for b in bars]
    atr = atr_wilder(bars)
    if atr is None or atr <= 0 or not math.isfinite(atr):
        return None
    fast, slow = ema_series(closes, 9), ema_series(closes, 21)
    rsis = state["rsi5"]["rsi"]
    common = {"bar_start": current_bar, "atr": atr, "stop": stop,
              "target2": target2, "policy_version": POLICY_VERSION}
    # A weak short reaction cannot alone negate the new frozen-thesis policy.
    allow_minor_exit=thesis_evidence is None or thesis_evidence.get('invalidated',False)
    if neutral_stall(closes, rsis, d) and allow_minor_exit:
        return dict(common, reason="neutral_exit")
    if (allow_minor_exit and d * (closes[-1] - fast[-1]) < 0
            and d * (closes[-1] - closes[-2]) < 0
            and d * (closes[-2] - closes[-3]) < 0):
        return dict(common, reason="trend_exit")
    trend = (d * (fast[-1] - slow[-1]) > 0
             and d * (fast[-1] - fast[-2]) > 0
             and d * (closes[-1] - fast[-1]) > 0
             and d * (state["closes15"][-1] - state["closes15"][-2]) > 0
             and d * (rsis[-1] - rsis[-2]) >= 0)
    post_entry = [b for b in bars if b["start"] >= sent_at]
    peak = (max(b["high"] for b in post_entry) if d == 1
            else min(b["low"] for b in post_entry))
    proposal = peak - d * 2 * atr
    new_stop = max(stop, entry, proposal) if d == 1 else min(stop, entry, proposal)
    # Never propose a new stop already crossed by the current observed quote.
    if d * (price - new_stop) < .1 * atr:
        new_stop = stop
    prior_volume = sum(b["volume"] for b in bars[-21:-1]) / 20
    volume_ratio = bars[-1]["volume"] / prior_volume if prior_volume > 0 else 0.
    barrier_cleared = (strategy != "range_rebound" or
                       (barrier is not None and d * (closes[-1] - barrier) >= .25 * atr))
    # Strong closed-bar evidence plus a quote near TP2 permits extension.
    # A range bounce may extend only after its opposite boundary is broken.
    if (trend and volume_ratio >= 1.0 and barrier_cleared
            and d * (price - target2) >= -.5 * atr
            and d * (new_stop - entry) >= 0):
        target = (max(target2, price) + 2 * atr if d == 1
                  else min(target2, price) - 2 * atr)
        if target > 0:
            return dict(common, reason="target_extended", stop=new_stop,
                        target2=target, volume_ratio=volume_ratio)
    if trend and d * (new_stop - stop) >= max(.25 * atr, entry * .0001):
        return dict(common, reason="trail_update", stop=new_stop, volume_ratio=volume_ratio)
    return None


def format_runner_alert(signal, reason, price, time_kst):
    side = "롱" if signal.direction == 1 else "숏"
    labels = {"target_extended": "추세 유지 · 목표 연장", "trail_update": "수익 보호선 조정",
              "trend_exit": "추세 약화 · 잔여 청산 검토", "neutral_exit": "중립 정체 · 잔여 청산",
              "time_exit": "추적 6시간 · 잔여 청산 검토"}
    proposal = signal.pending_runner or {}
    stop, target = proposal.get("stop", signal.stop), proposal.get("target2", signal.target2)
    fraction = "50%" if signal.target1_notified else "물량"
    action = ("실제 잔여량 확인 후 보호 손절·목표 주문을 직접 조정하세요."
              if reason in ("target_extended", "trail_update") else
              "실제 보유 중이면 잔여 물량 청산을 확인하세요. 신규 진입 신호가 아닙니다.")
    return (f"[LBank BTCUSDT | {labels[reason]} - {side}]\n"
            f"신호 #{signal.number} | {time_kst} KST\n"
            f"현재 {price:,.1f} USDT | 보호 손절 {stop:,.1f} USDT\n"
            f"잔여 {fraction} 목표 {target:,.1f} USDT\n"
            f"{action}\n"
            "체결·보유 미확인, 자동주문 없음. 이미 청산했다면 적용하지 마세요.")
