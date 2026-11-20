"""Research-only barrier-aware B-short/long audit.

This module never approves orders, sends Telegram, or bypasses the formal
entry review lock. A historical pivot in front of a far target remains a
hard block; separate first-barrier and confirmed-rebreak lanes are journaled.
All inputs must have been observable at the given evaluation time.
"""
import math
from research.zb_counterfactual_replay import assess_geometry

POLICY = "zb_barrier_lanes_shadow_v2"


def _valid(*values):
    return all(isinstance(x, (float, int)) and not isinstance(x, bool)
               and math.isfinite(x) and x > 0 for x in values)


def _assess(side, entry, stop, target, fee_rate, exit_slippage, min_net_rr):
    check = assess_geometry(side, entry, stop, target, fee_fraction=fee_rate,
                            exit_slippage_fraction=exit_slippage, min_net_rr=min_net_rr)
    status = "net_rr_pass_shadow" if check["status"] == "qualified_shadow" else check["status"]
    return dict(status=status, net_rr=check.get("net_rr"),
                net_gain_per_unit=check.get("net_gain_per_btc"),
                net_loss_per_unit=check.get("net_loss_per_btc"), target=target)


def evaluate_barrier_lanes(side, entry, stop, barrier, far_target,
                           closed_minutes=(), observed_at=None,
                           next_target=None, next_target_known_at=None, fee_rate=0.0006,
                           exit_slippage=0.0003, min_net_rr=2.0):
    """Evaluate two distinct research paths without mutating an entry gate.

    Lane 1: exit at the *first known* obstacle rather than presume that a
    distant target is reachable. Lane 2: require an observed completed
    1-minute break and failed reclaim before evaluating a fresh risk plan.
    For lane 2 the next target MUST be supplied from prior known structure.
    No new order authorization or target fabrication is possible here.
    """
    result = dict(policy=POLICY, shadow_only=True, entry_approved=False,
                  first_barrier={"status": "invalid"}, continuation={"status": "invalid"})
    if (side not in (-1, 1) or not _valid(entry, stop, barrier, far_target)
            or not all(math.isfinite(v) and 0 <= v < 1 for v in (fee_rate, exit_slippage))
            or not math.isfinite(min_net_rr) or min_net_rr < 1):
        return result
    if side*(entry-stop) > 0 and side*(barrier-entry) > 0 and side*(far_target-barrier) > 0:
        result["first_barrier"] = _assess(
            side, entry, stop, barrier, fee_rate, exit_slippage, min_net_rr)
        result["first_barrier"]["exit_assumption"] = "all_out_at_first_barrier"
    result["continuation"] = barrier_retest(side, barrier, closed_minutes, observed_at,
        next_target=next_target, next_target_known_at=next_target_known_at,
        fee_rate=fee_rate, exit_slippage=exit_slippage, min_net_rr=min_net_rr)
    return result


def barrier_retest(side, barrier, closed_minutes, observed_at, *, next_target=None,
                   next_target_known_at=None, fee_rate=.0006, exit_slippage=.0003,
                   min_net_rr=2.):
    """Assess a new break/retest on its own, independent of the initial entry."""
    cont = {"status": "awaiting_confirmed_break_and_retest", "net_rr": None}
    if side not in (-1,1) or not _valid(barrier, observed_at) or len(closed_minutes) < 2:
        return cont
    first, second = closed_minutes[-2:]
    try:
        if (not math.isfinite(observed_at) or
                not all(_valid(b["open"], b["high"], b["low"], b["close"])
                    and b["low"] <= min(b["open"], b["close"])
                    <= max(b["open"], b["close"]) <= b["high"]
                    and type(b["start"]) is int and b["start"] % 60 == 0
                    and b.get("period", 60) == 60
                    and type(b["received_at"]) in (int, float)
                    and b.get("quality") == "stream_closed"
                    and b["start"] + 60 <= b["received_at"] <= observed_at
                    for b in (first, second))
                or second["start"] != first["start"] + 60):
            cont["status"] = "invalid_or_future_candles"
            return cont
    except (KeyError, TypeError, ValueError):
        cont["status"] = "invalid_or_future_candles"
        return cont
    buffer = barrier * 0.00015
    broke = side * (first["close"] - barrier) > buffer
    retest = (second["low"] <= barrier + buffer if side == 1
              else second["high"] >= barrier - buffer)
    held = side * (second["close"] - barrier) > buffer
    if not (broke and retest and held):
        return cont
    cont["structure_seen"] = True
    if next_target is None or not _valid(next_target) or next_target_known_at is None:
        cont["status"] = "missing_prior_structural_target"
        return cont
    if not _valid(next_target_known_at) or next_target_known_at > first["start"]:
        cont["status"] = "target_not_known_before_break"
        return cont
    renewed_entry = second["close"]
    if side * (next_target - renewed_entry) <= 0:
        cont["status"] = "target_not_ahead"
        return cont
    edge = "high" if side == 1 else "low"
    if any(side * (bar[edge] - next_target) >= 0 for bar in (first, second)):
        cont["status"] = "target_already_touched"
        return cont
    # Preserve the observed retest extreme as the stop anchor; never narrow
    # the stop merely to make risk/reward look better.
    renewed_stop = (min(first["low"], second["low"]) - buffer if side == 1
                    else max(first["high"], second["high"]) + buffer)
    if side * (renewed_stop - renewed_entry) >= 0:
        cont["status"] = "invalid_stop_geometry"
        return cont
    cont.update(_assess(side, renewed_entry, renewed_stop, next_target, fee_rate,
                        exit_slippage, min_net_rr),
                entry=renewed_entry, stop=renewed_stop,
                target_known_at=next_target_known_at)
    return cont
