"""Build reproducible ZB observation and risk plans from as-of market snapshots."""
import math

from lbank_early_move_shadow import early_move_shadow
from lbank_entry_quality import confirmed_pivot_barriers
from lbank_five_timeframe_shadow import five_timeframe_shadow, valid_observed_bar
from lbank_trend_runner import atr_wilder
from research.zb_barrier_lanes_shadow import barrier_retest
from research.zb_counterfactual_replay import assess_geometry

POLICY = "zb_signal_research_v2"
COSTS = dict(fee_fraction=.0006, exit_slippage_fraction=.0003, funding_reserve_fraction=.0001)
MAIN_STOP_CAP = .006
STUDY_STOP_CAP = .008
MIN_NET_RR = 2.
MAX_CHASE = .007
MAX_QUOTE_DEVIATION = .001
PIVOT_BUFFER = .00015


def _visible_tail(bars, period, now, count):
    expected = int(now//period)*period-period
    tail = []
    for bar in reversed(bars[-count:]):
        if bar["start"] != expected or not valid_observed_bar(bar, period, now):
            break
        tail.append(bar)
        expected -= period
    return list(reversed(tail))


def _targets(fives, minutes, side, price, known_by):
    # Two completed bars to the right certify a pivot. Ignore obstacles
    # already traded through, including after the latest 5m close.
    index = {b["start"]: i for i,b in enumerate(fives)}
    targets = []
    edge = "high" if side == 1 else "low"
    tail = [b for b in minutes if fives and b["start"] >= fives[-1]["start"]+300]
    for pivot in confirmed_pivot_barriers(fives, side, price):
        i = index[pivot["start"]]
        confirmed = max(b["received_at"] for b in fives[i-2:i+3])
        level = pivot["price"]
        if confirmed > known_by:
            continue
        if any(side*(b[edge]-level) > 0 for b in fives[i+3:]+tail):
            continue
        targets.append((level, confirmed))
    return sorted(targets, key=lambda p: side*p[0])


def _support_recovery(minutes, fives):
    """Observe a closed one-minute recovery at previously tested 5m support."""
    previous, latest = minutes[-2:]
    if (latest["close"] <= max(latest["open"], previous["close"])
            or (previous["close"] >= minutes[-6]["close"] and latest["low"] >= previous["low"])):
        return None

    # Freeze the support before the two minutes that are testing it.
    known = [b for b in fives[-24:] if b["received_at"] <= previous["start"]]
    tested_low = min(previous["low"], latest["low"])
    for base in sorted(known, key=lambda b: (abs(b["low"]-tested_low), -b["start"])):
        level = base["low"]
        width = 2 * PIVOT_BUFFER * level
        trigger = level+width
        if (abs(tested_low-level) > width or latest["close"] <= trigger
                or latest["close"]-latest["open"] < PIVOT_BUFFER*level):
            continue
        if (latest["low"] > trigger and previous["close"] > trigger
                and previous["close"] > previous["open"]):
            continue  # The preceding minute already recovered this level.
        tests = [b for b in known if abs(b["low"]-level) <= width and b["close"] > trigger]
        if len(tests) < 2 or any(b["close"] < level-width for b in known if b["start"] > tests[-1]["start"]):
            continue
        return dict(lane="C1", direction=1, level=level, trigger=trigger,
                    signal_basis="closed_minute_support_recovery", tested_low=tested_low,
                    support_five_starts=[b["start"] for b in tests],
                    support_known_at=max(b["received_at"] for b in tests))
    return None


def _plan(candidate, quote, quote_time, now, minutes, fives, targets, stop_style):
    side, latest = candidate["direction"], minutes[-1]
    atr1, atr5 = atr_wilder(minutes), atr_wilder(fives)
    valid_quote = (type(quote) in (int, float) and math.isfinite(quote) and quote > 0
                   and type(quote_time) in (int, float)
                   and latest["received_at"] <= quote_time <= now
                   and now-quote_time <= 15)
    reference = quote if valid_quote else latest["close"]
    entry = reference*(1+side*COSTS["exit_slippage_fraction"])
    structural = minutes[-8:] if stop_style == "1m" else fives[-3:]
    plan = dict(entry=entry, reference_price=reference,
                reference_is_fresh_quote=valid_quote, atr1=atr1, atr5=atr5,
                stop_basis=stop_style, stop=None, target=None, net_rr=None)
    atr = atr1 if stop_style == "1m" else atr5
    if atr is None or not structural:
        return dict(plan, status="unverified_atr_or_structure", cap_080_status="unverified_atr_or_structure")
    invalidation = min(b["low"] for b in structural) if side == 1 else max(b["high"] for b in structural)
    anchor_stop = invalidation*(1-side*.0003)
    noise_stop = reference-side*max(.0005*reference, 1.5*atr)
    stop = min(anchor_stop, noise_stop) if side == 1 else max(anchor_stop, noise_stop)
    plan.update(stop=stop, stop_fraction=abs(entry-stop)/entry, structure_invalidation=invalidation)
    if not targets:
        return dict(plan, status="no_known_target", cap_080_status="no_known_target")
    first, known_at = targets[0]
    target = first*(1-side*PIVOT_BUFFER)
    geometry = assess_geometry(side, entry, stop, target, **COSTS,
                               min_net_rr=MIN_NET_RR, max_stop_fraction=MAIN_STOP_CAP)
    sensitivity = assess_geometry(side, entry, stop, target, **COSTS,
                                  min_net_rr=MIN_NET_RR, max_stop_fraction=STUDY_STOP_CAP)
    plan.update(geometry, target=target, first_barrier=first, target_known_at=known_at,
                far_target=next((p for p,_ in targets if side*(p-first)>0), None),
                exit_assumption="all_out_at_first_barrier", cap_080_status=sensitivity["status"])
    trigger = candidate["trigger"]
    late_closed_impulse = len(fives) >= 4 and side*(fives[-1]["close"]/fives[-4]["close"]-1) > MAX_CHASE
    quote_gate = ("no_fresh_quote" if not valid_quote else
                  "price_deviation" if abs(quote/latest["close"]-1) > MAX_QUOTE_DEVIATION else
                  "structure_lost" if side*(quote-trigger) <= 0 else
                  "avoid_late_5m_impulse_chase" if late_closed_impulse else
                  "late_chase" if side*(quote/trigger-1) >= MAX_CHASE else None)
    if quote_gate:
        plan.update(status=quote_gate, cap_080_status=quote_gate)
    return plan


def evaluate_snapshot(state, now):
    """Evaluate A, B1, B2, C1 and C2 independently, using observable prices."""
    context = five_timeframe_shadow(state, now)
    result = dict(policy=POLICY, evaluated_at=now, shadow_only=True,
                  entry_approved=False, timeframes=context, candidates=[],
                  status="unverified_native_minutes")
    minutes = _visible_tail(state.get("minute_bars", []), 60, now, 20)
    if len(minutes) < 20:
        return result
    fives = _visible_tail(state.get("five_bars", []), 300, now, 250)
    quote, quote_time = state.get("quote"), state.get("quote_observed_at")
    dmap = {"up": 1, "down": -1}
    trend15 = dmap.get(context["15m"]["direction"])
    trend60 = dmap.get(context["1h"]["direction"])
    prior, previous, latest = minutes[:-2], minutes[-2], minutes[-1]
    result.update(minute_start=latest["start"], minute_received_at=latest["received_at"],
                  basis_price=quote, status="no_setup", early_move_checks={})
    candidates = result["candidates"]
    resistance = max(b["high"] for b in prior)
    if (all(b["received_at"] <= latest["start"] for b in prior)
            and latest["high"] >= resistance*(1-PIVOT_BUFFER)
            and latest["close"] < min(latest["open"], previous["close"], resistance*(1-PIVOT_BUFFER))):
        candidates.append(dict(lane="A", direction=-1, level=resistance, trigger=resistance,
                               signal_basis="observed_resistance_rejection"))
    for side in (-1, 1):
        early = early_move_shadow(minutes, now, direction=side, trend15=trend15, trend60=trend60)
        result["early_move_checks"][str(side)] = early["status"]
        if early["status"] == "early_break_shadow":
            reversal = ("down" in (context["5m"]["direction"], context["15m"]["direction"])
                        or (latest["close"] > minutes[-6]["close"] and
                            previous["close"] < minutes[-7]["close"]))
            lane = "B1" if side == -1 else "C1" if reversal else "C2"
            candidates.append(dict(lane=lane, direction=side, level=early["level"],
                trigger=early["level"], signal_basis="first_closed_minute_break",
                first_break_pct=early["break_pct"]))
        # Freeze the level before the first of the two retest bars.
        prepared = [b for b in prior if b["received_at"] <= previous["start"]]
        if len(prepared) < 9:
            continue
        edge = "high" if side == 1 else "low"
        level = (max if side == 1 else min)(b[edge] for b in prepared)
        if side*(prior[-1]["close"]/level-1) > PIVOT_BUFFER:
            continue
        targets = _targets(fives, minutes, side, latest["close"], previous["start"])
        buffered, known = ((targets[0][0]*(1-side*PIVOT_BUFFER), targets[0][1])
                           if targets else (None, None))
        retest = barrier_retest(side, level, minutes, now, next_target=buffered,
                                next_target_known_at=known)
        if retest.get("structure_seen") and side*(latest["close"]/level-1) < MAX_CHASE:
            candidates.append(dict(lane="B2" if side == -1 else "C2", direction=side,
                level=level, trigger=level, signal_basis="break_and_held_retest",
                retest_status=retest["status"]))
    support_recovery = _support_recovery(minutes, fives)
    if support_recovery and not any(item["lane"] == "C1" for item in candidates):
        candidates.append(support_recovery)
    for item in candidates:
        cutoff = previous["start"] if item["signal_basis"] == "break_and_held_retest" else now
        targets = _targets(fives, minutes, item["direction"], latest["close"], cutoff)
        if item.get("retest_status") == "target_already_touched":
            targets = []
        item["plans"] = {basis: _plan(item, quote, quote_time, now, minutes, fives, targets, basis)
                         for basis in ("1m", "5m")}
        item["best_plan"] = max(item["plans"], key=lambda basis: (
            item["plans"][basis]["status"] == "qualified_shadow",
            (item["plans"][basis]["net_rr"] if item["plans"][basis]["net_rr"] is not None
             else -math.inf)))
    if candidates:
        candidates.sort(key=lambda item: (
            item["plans"][item["best_plan"]]["status"] == "qualified_shadow",
            (item["plans"][item["best_plan"]]["net_rr"]
             if item["plans"][item["best_plan"]]["net_rr"] is not None else -math.inf)), reverse=True)
        result["status"] = "candidates_observed"
    return result
