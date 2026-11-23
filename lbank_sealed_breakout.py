"""Evidence for a fresh sealed-minute break; never an entry approval or sender.

Only the stale flat-close veto can use this evidence. All other context,
cost, stop, target, confirmation and daily-risk gates remain authoritative.
"""
import math
from statistics import median

from lbank_trend_route import _bar_error

POLICY = "sealed_minute_breakout_context_v1"
SOURCE = "lbank_native_1m_aggregated_5m"


def sealed_breakout(state, direction, level):
    # NativeEntryFeed is imported by the ATR module during monitor startup.
    from lbank_entry_refinement import minute_atr
    empty = dict(policy=POLICY, detected=False, direction=direction)
    try:
        if direction not in (-1, 1) or state.get("source") != SOURCE:
            return empty
        bars, five, prices = state["minute_bars"], state["five_bars"], state["prices"]
        now, level = float(state["observed_at"]), float(level)
        if (len(bars) < 65 or len(five) < 20 or len(prices) != len(bars)
                or not math.isfinite(now) or not math.isfinite(level) or level <= 0
                or _bar_error(bars, 60) or _bar_error(five, 300)
                or any(float(b["close"]) != float(p) for b, p in zip(bars, prices))):
            return empty
        last = bars[-1]
        # Require the latest completed minute/5m at the actual observation.
        # A quality label alone cannot authorize a forming or future candle.
        if (last["start"] != int(now // 60) * 60 - 60
                or five[-1]["start"] != int(now // 300) * 300 - 300):
            return empty
        atr = minute_atr(state)
        if atr is None:
            return empty
        d, close = direction, float(last["close"])
        prior = bars[-16:-1]
        wick = (max(b["high"] for b in prior) if d == 1
                else min(b["low"] for b in prior))
        boundary = max(wick, level) if d == 1 else min(wick, level)
        volumes = [float(b["volume"]) for b in bars[-16:]]
        if any(not math.isfinite(v) or v < 0 for v in volumes):
            return empty
        baseline = median(volumes[:-1])
        detected = (baseline > 0 and volumes[-1] >= 1.1 * baseline
                    and d * (close - boundary) >= max(.00002 * close, .05 * atr)
                    and d * (close - float(last["open"])) >= max(.00025 * close, .65 * atr)
                    and d * (close - float(prices[-6])) > 0
                    and d * (close - float(prices[-16])) > 0)
        return dict(empty, detected=detected, source=SOURCE,
                    last_closed_1m_start=last["start"], closed_at=last["start"] + 60,
                    close=close, boundary=boundary, atr1=atr,
                    volume_multiple=volumes[-1] / baseline if baseline else None)
    except (KeyError, TypeError, ValueError, OverflowError, IndexError):
        return empty


def restore_retry(raw, now):
    """An invalid/old cache grants no retry; never reset a used retry to zero."""
    try:
        if not isinstance(raw, dict) or type(raw["used"]) is not int or raw["used"] not in (0, 1):
            return None
        origin, sent, entry = (float(raw[k]) for k in ("origin_at", "sent_at", "entry"))
        stopped = raw["stopped_at"]
        if (raw["direction"] not in (-1, 1) or entry <= 0
                or not all(math.isfinite(x) for x in (origin, sent, entry, now))
                or not 0 <= origin <= sent <= now or now - origin >= 2700):
            return None
        if stopped is not None:
            stopped = float(stopped)
            if not math.isfinite(stopped) or not sent <= stopped <= now:
                return None
        return dict(origin_at=origin, sent_at=sent, entry=entry,
                    direction=raw["direction"], used=raw["used"], stopped_at=stopped)
    except (KeyError, TypeError, ValueError, OverflowError):
        return None


def arm_stopped_retry(state, tracked, reason, now):
    """Only a stop notice for the latest sent reference can arm one retry."""
    retry = restore_retry(state.get("entry_retry"), now)
    if (reason != "stop_breached" or retry is None or retry["used"]
            or retry["sent_at"] != state.get("last_time")
            or retry["sent_at"] != tracked.sent_at
            or retry["direction"] != tracked.direction):
        return False
    state["entry_retry"] = dict(retry, stopped_at=now)
    return True


def stopped_retry_allowed(now, side, state, plan):
    """A NEW post-stop sealed breakout may retry once inside the 45m cooldown."""
    retry = restore_retry(state.get("entry_retry"), now)
    if not plan or not retry or retry["used"] or retry["stopped_at"] is None:
        return False
    d = retry["direction"]
    proof = plan.get("sealed_breakout_evidence") or {}
    try:
        return (side == state.get("last_side") == ("롱" if d == 1 else "숏")
                and retry["sent_at"] == state.get("last_time")
                and plan.get("strategy") == "breakout" and plan.get("direction") == d
                and proof.get("policy") == POLICY and proof.get("source") == SOURCE
                and proof.get("detected") is True and proof.get("direction") == d
                and proof["last_closed_1m_start"] >= retry["stopped_at"]
                and 0 <= now - proof["closed_at"] <= 90
                and d * (float(proof["close"]) - retry["entry"]) > 0
                and d * (float(plan["price"]) - retry["entry"]) > 0)
    except (KeyError, TypeError, ValueError, OverflowError):
        return False


def record_retry_state(now, state, plan, used_retry):
    if not plan or plan.get("direction") not in (-1, 1):
        state.pop("entry_retry", None)
        return
    previous = restore_retry(state.get("entry_retry"), now)
    state["entry_retry"] = dict(origin_at=previous["origin_at"] if used_retry else now,
        sent_at=now, entry=float(plan["price"]), direction=plan["direction"],
        used=1 if used_retry else 0, stopped_at=None)
