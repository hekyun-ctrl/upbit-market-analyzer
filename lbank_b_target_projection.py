"""Causal, bounded cost-aware *hypothetical* short target, not support.

A projected target is never a verified pivot or a profitability guarantee.
Only use as a test candidate; confirmed barriers still take priority.
"""
import math
from lbank_structural_target_plan import rounded
from research.zerobase.video_entry import Costs

def project_short_target(entry, stop, atr, *, costs=None, desired_rr=2.05, max_atr=8.0):
    """Return a 2R-cost-aware volatility target or an explicit rejection."""
    costs = costs or Costs(min_rr=2.0)
    values = (entry, stop, atr, desired_rr, max_atr)
    if not all(type(v) in (float, int) and math.isfinite(v) for v in values):
        return {"status": "invalid_projection_inputs"}
    if not (entry > 0 and stop > entry and atr > 0 and desired_rr >= costs.min_rr and max_atr > 0):
        return {"status": "invalid_projection_inputs"}
    loss = -costs.pnl(-1, entry, stop, exit_kind="stop")
    if not math.isfinite(loss) or loss <= 0:
        return {"status": "invalid_stop_loss"}
    # Binary search a hypothetical exit price. No future or unverified pivot used.
    lo, hi = 0.0, entry
    for _ in range(52):
        mid = (lo + hi) / 2
        if costs.pnl(-1, entry, mid, exit_kind="take_profit") >= desired_rr * loss:
            lo = mid
        else:
            hi = mid
    target = rounded(lo, costs.tick, up=False)
    if not 0 < target < entry:
        return {"status": "invalid_projected_target"}
    distance_atr = (entry - target) / atr
    if distance_atr > max_atr:
        return {"status": "projection_outside_volatility_cap",
                "target": target, "distance_atr": distance_atr}
    net_rr = costs.pnl(-1, entry, target, exit_kind="take_profit") / loss
    if net_rr < costs.min_rr:
        return {"status": "insufficient_net_rr", "target": target, "net_rr": net_rr}
    return {"status": "projected_target_candidate", "target": target,
            "net_rr": net_rr, "distance_atr": distance_atr,
            "target_basis": "bounded_volatility_projection_NOT_known_support",
            "evidence_grade": "UNVALIDATED_research_only"}
