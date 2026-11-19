"""Research-only barrier-aware B-short/long audit.

This module never approves orders, sends Telegram, or bypasses the formal
entry review lock. A historical pivot in front of a far target remains a
hard block; separate first-barrier and confirmed-rebreak lanes are journaled.
All inputs must have been observable at the given evaluation time.
"""
import math

POLICY = "zb_barrier_lanes_shadow_v1"


def _valid(*values):
    return all(isinstance(x, (float, int)) and not isinstance(x, bool)
               and math.isfinite(x) and x > 0 for x in values)


def _net_rr(side, entry, stop, target, fee_rate, exit_slippage):
    """Conservative two-taker-fee and adverse exit slippage approximation."""
    loss = -side * (stop - entry) + fee_rate * (entry + stop) + exit_slippage * stop
    gain = side * (target - entry) - fee_rate * (entry + target) - exit_slippage * target
    return (gain / loss if loss > 0 else None), gain, loss


def evaluate_barrier_lanes(side, entry, stop, barrier, far_target,
                           closed_minutes=(), observed_at=None,
                           next_target=None, fee_rate=0.0006,
                           exit_slippage=0.0003, min_net_rr=2.0):
    """Evaluate two distinct research paths without mutating an entry gate.

    Lane 1: exit at the *first known* obstacle rather than presume that a
    distant target is reachable. Lane 2: require an observed completed
    1-minute break and failed reclaim before evaluating a fresh risk plan.
    For lane 2 the next target MUST be supplied from prior known structure.
    No new order authorization or target fabrication is possible here.
    """
    result = dict(policy=POLICY, shadow_only=True, entry_approved=False,
                  first_barrier={"status": "invalid"},
                  continuation={"status": "invalid"})
    if (side not in (-1, 1) or not _valid(entry, stop, barrier, far_target)
            or not all(math.isfinite(v) and v >= 0 for v in (fee_rate, exit_slippage))
            or not math.isfinite(min_net_rr) or min_net_rr < 1
            or side * (entry - stop) >= 0
            or side * (barrier - entry) <= 0
            or side * (far_target - barrier) <= 0):
        return result
    rr, gain, loss = _net_rr(side, entry, stop, barrier, fee_rate, exit_slippage)
    result["first_barrier"] = {
        "status": ("net_rr_pass_shadow" if rr is not None and rr >= min_net_rr
                   else "insufficient_net_rr"),
        "net_rr": rr, "net_gain_per_unit": gain,
        "net_loss_per_unit": loss, "target": barrier,
    }
    cont = {"status": "awaiting_confirmed_break_and_retest", "net_rr": None}
    result["continuation"] = cont
    if len(closed_minutes) < 2 or observed_at is None:
        return result
    first, second = closed_minutes[-2:]
    try:
        if (not all(_valid(b["open"], b["high"], b["low"], b["close"])
                    and b["low"] <= min(b["open"], b["close"])
                    <= max(b["open"], b["close"]) <= b["high"]
                    and isinstance(b["start"], int)
                    and isinstance(b["received_at"], (int, float))
                    and b.get("quality") == "stream_closed"
                    and b["start"] + 60 <= b["received_at"] <= observed_at
                    for b in (first, second))
                or second["start"] != first["start"] + 60):
            cont["status"] = "invalid_or_future_candles"
            return result
    except (KeyError, TypeError, ValueError):
        cont["status"] = "invalid_or_future_candles"
        return result
    buffer = barrier * 0.00015
    broke = side * (first["close"] - barrier) > buffer
    retest = (second["low"] <= barrier + buffer if side == 1
              else second["high"] >= barrier - buffer)
    held = side * (second["close"] - barrier) > buffer
    if not (broke and retest and held):
        return result
    if next_target is None or not _valid(next_target):
        cont["status"] = "missing_prior_structural_target"
        return result
    renewed_entry = second["close"]
    if side * (next_target - renewed_entry) <= 0:
        cont["status"] = "target_not_ahead"
        return result
    # Preserve the observed retest extreme as the stop anchor; never narrow
    # the stop merely to make risk/reward look better.
    renewed_stop = (min(first["low"], second["low"]) - buffer if side == 1
                    else max(first["high"], second["high"]) + buffer)
    if side * (renewed_entry - renewed_stop) >= 0:
        cont["status"] = "invalid_stop_geometry"
        return result
    rr2, gain2, loss2 = _net_rr(side, renewed_entry, renewed_stop,
                                next_target, fee_rate, exit_slippage)
    cont.update(status=("net_rr_pass_shadow" if rr2 is not None and rr2 >= min_net_rr
                        else "insufficient_net_rr"),
                entry=renewed_entry, stop=renewed_stop, target=next_target,
                net_rr=rr2, net_gain_per_unit=gain2,
                net_loss_per_unit=loss2)
    return result
