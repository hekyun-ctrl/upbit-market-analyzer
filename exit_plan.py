"""Planned reward, not a probability or an estimate of realized returns."""

from __future__ import annotations

from math import isfinite
from typing import Any


def exit_plan_metrics(
    entry: float, stop: float, target_1: float, target_2: float,
    plan: dict[str, Any] | None = None,
) -> dict[str, float | str]:
    """Use identical, unrounded admission math before and after the wait.

    A partial plan counts only its conservative runner ceiling. It never
    borrows the 10/15/20 percent observation lines as available reward.
    """
    if not all(isfinite(x) for x in (entry, stop, target_1, target_2)):
        raise ValueError("non-finite exit plan")
    risk = entry - stop
    if stop <= 0 or risk <= 0 or target_1 <= entry:
        raise ValueError("invalid exit plan ordering")
    first_rr = (target_1 - entry) / risk
    if not plan:
        return {"risk_reward_basis": "target_1", "risk_reward": first_rr,
                "first_target_risk_reward": first_rr}
    if (plan.get("mode") != "partial_50_50"
            or plan.get("target_1_fraction") != 0.5
            or plan.get("runner_fraction") != 0.5):
        raise ValueError("unsupported partial exit plan")
    ceiling = float(plan["runner_ceiling"])
    if not isfinite(ceiling) or not entry < target_1 < target_2 <= ceiling:
        raise ValueError("runner crosses conservative resistance ceiling")
    reward = 0.5 * (target_1 - entry) + 0.5 * (target_2 - entry)
    return {"risk_reward_basis": "partial_exit_plan", "risk_reward": reward / risk,
            "first_target_risk_reward": first_rr}


def modeled_exit_return(
    entry: float, stop: float, target_1: float, target_2: float,
    target_1_reached: bool, exit_price: float,
) -> dict[str, float | str]:
    """A gross hypothetical 50/50 path, with observed adverse exit slippage.

    This is not the user's trade: target fills, fees and execution are unknown.
    The second half is capped at its planned exit even on a price gap upward.
    """
    if target_1_reached:
        gain = 0.5 * (target_1 - entry) + 0.5 * (min(exit_price, target_2) - entry)
    else:
        gain = exit_price - entry
    return {"modeled_plan_return_pct": round(gain / entry * 100, 4),
            "modeled_plan_r": round(gain / (entry - stop), 4),
            "modeled_plan_definition": "50/50 계획 가정·수수료 전·실제 체결 손익 아님"}
