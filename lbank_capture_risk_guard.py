"""Narrow causal safety gates for capture-mode entry reviews.

No trade orders. No fitted win probabilities. Historical incident tests use
recorded decision evidence, not simulated fills or future candles.
"""
import math

POLICY = "capture_entry_risk_v1"


def entry_guard(plan, watch, native_state, *, stopped_retry=False, breakout_followthrough=False):
    """Return (allowed, reason, evidence); no universal extra wait.

    A nearby observed obstacle cannot be waived by higher trend ranking.
    Weak context or post-stop retries need an independent completed minute.
    A missing proof is never interpreted as positive evidence.
    """
    try:
        d = int(plan["direction"])
        entry, stop = float(plan["price"]), float(plan["stop"])
        quality = plan["entry_quality"]
        priority = quality["priority"]
        ranked = plan["entry_decision_priority"]
        if (d not in (-1, 1) or not all(math.isfinite(x) and x > 0 for x in (entry, stop))
                or d*(entry-stop) <= 0 or not priority.get("ready") or not ranked.get("ready")):
            return False, "capture_guard_evidence_missing", {}
        risk = max(abs(entry-stop), float(plan["net_loss"])/float(plan["quantity_btc"]))
        if not math.isfinite(risk) or risk <= 0:
            return False, "capture_guard_evidence_missing", {}
        room = priority.get("target_room")
        barrier = priority.get("observed_barrier")
        distance = None if barrier is None else d*(float(barrier)-entry)
        if distance is not None and (not math.isfinite(distance) or distance <= 0):
            return False, "capture_guard_evidence_missing", {}
        higher = ranked.get("higher_direction_intact") is True
        # Use only the same post-watch completed-minute set validated by
        # confirmation_quality; a bar already used at stage one is not new.
        starts = quality.get("confirmed_1m_starts", [])
        start = watch["native_watch_1m_start"]
        quote = float(native_state["quote"])
        watch_price = float(watch["price"])
        is_breakout = watch.get("strategy") == "breakout"
        level = watch.get("level") if is_breakout else None
        rows = [b for b in native_state["minute_bars"]
                if b["start"] in starts and b["start"] >= start+120]
        fresh = False
        if rows:
            last = rows[-1]
            o, c = float(last["open"]), float(last["close"])
            fresh = (last["quality"] == "stream_closed" and last["start"] % 60 == 0
                     and all(math.isfinite(x) and x > 0 for x in (o,c,quote,watch_price))
                     and d*(c-o) > 0 and d*(c-watch_price) > 0
                     and d*(quote-c) >= 0
                     and (level is None or (d*(c-float(level)) > 0 and d*(quote-float(level)) > 0)))
        dominant = priority.get("dominant_intact") is True
        # Do not accept a stalled or adverse quote in unproven swing context.
        signed_progress = d * (quote - watch_price)
        if not all(math.isfinite(x) and x > 0 for x in (quote, watch_price)):
            return False, "capture_guard_evidence_missing", {}
        evidence = dict(policy=POLICY, higher_direction_intact=higher,
                        dominant_intact=dominant, signed_quote_progress=signed_progress,
                        target_room=room, barrier_distance=distance, unit_risk=risk,
                        fresh_completed_minute=bool(fresh), stopped_retry=bool(stopped_retry))
        if room == "conflict" and (distance is None or distance <= risk):
            return False, "capture_guard_near_target_barrier", evidence
        if stopped_retry and not fresh:
            return False, "capture_guard_retry_wait_fresh_minute", evidence
        if (not higher or room == "conflict") and not fresh:
            return False, "capture_guard_wait_fresh_minute", evidence
        # Incident 92: aligned EMA/15m alone allowed a falling LONG quote,
        # with no dominant structure, known target room or fresh minute.
        if not dominant and room == "unknown" and signed_progress <= 0 and not fresh:
            return False, "capture_guard_unproven_room_wait_progress", evidence
        # This stricter research check depends on the observed structure, not
        # on whether a preceding setup happened to produce a sent alert.
        if breakout_followthrough and is_breakout and priority.get("dominant_intact") is not True and not fresh:
            return False, "capture_shadow_breakout_wait_fresh_minute", evidence
        return True, "capture_guard_pass", evidence
    except (KeyError, TypeError, ValueError, IndexError, ZeroDivisionError, OverflowError):
        return False, "capture_guard_evidence_missing", {}


def caution_kind(policy, state, *, direction, entry, stop, current, thesis_status):
    """One-shot advisory only; original stop and invalidation take priority.

    0.75R adverse quote warns before a hard stop when observed. A profitable
    excursion above modeled break-even followed by a >=50% net giveback and
    weakened/unknown thesis warns independently of the 1R profit-trail arm.
    Thresholds are conservative hypotheses, not backtested parameters.
    """
    if not isinstance(policy, dict) or policy.get("capture_guard_policy") != POLICY:
        return None
    try:
        d = direction
        entry, stop, current = map(float, (entry, stop, current))
        be = float(policy["breakeven"])
        best = float((state or {}).get("best", entry))
        if (d not in (-1, 1) or not all(math.isfinite(x) and x > 0
                for x in (entry,stop,current,be,best)) or d*(entry-stop) <= 0
                or d*(current-stop) <= 0):
            return None
        if -d*(current-entry) >= .75*abs(entry-stop):
            return "pre_stop_risk"
        net_peak = d*(best-be)
        if (thesis_status in ("weakened","unknown") and net_peak > 0
                and d*(best-current) >= .5*net_peak):
            return "pre_target_profit_giveback"
    except (KeyError, TypeError, ValueError, OverflowError):
        return None
    return None
