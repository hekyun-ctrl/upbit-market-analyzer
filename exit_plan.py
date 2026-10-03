"""Planned reward, not a probability or an estimate of realized returns."""

from __future__ import annotations

from math import isfinite
from statistics import mean
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


def execution_cost_metrics(entry, stop, target_1, target_2, plan, spread_pct, buffer_pct=0.2):
    """Conservative model: one full spread plus an explicit fee/slippage buffer.

    Costs reduce reward and increase loss; hypothetical fills remain unknown.
    Partial plans use their existing conservative ceiling, never expansion lines.
    """
    if not all(isfinite(x) and x >= 0 for x in (spread_pct, buffer_pct)):
        raise ValueError("invalid execution cost assumption")
    gross = exit_plan_metrics(entry, stop, target_1, target_2, plan)
    cost = entry * (spread_pct + buffer_pct) / 100
    risk = entry - stop
    return {
        "estimated_round_trip_cost_pct": spread_pct + buffer_pct,
        "net_risk_reward": (float(gross["risk_reward"]) * risk - cost) / (risk + cost),
        "net_first_target_risk_reward": (target_1 - entry - cost) / (risk + cost),
        "execution_cost_basis": "full_observed_spread_plus_fee_slippage_buffer",
    }


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


def partial_plan_summary(outcomes: list[dict[str, Any]], cost_pct: float = 0.2) -> dict[str, Any]:
    """Completed 50/50 plans only; a first target alone is not a full exit.

    Cost is an assumed round-trip percentage of initial position notional,
    including all partial exits. It is not a claim about the user's fees.
    """
    if not isfinite(cost_pct) or cost_pct < 0:
        raise ValueError("invalid assumed round-trip cost")
    rows = [row for row in outcomes if row.get("exit_plan")]
    net_returns, net_rs = [], []
    for row in rows:
        try:
            entry, stop = float(row["entry_price"]), float(row["stop_price"])
            gross = float(row["modeled_plan_return_pct"])
            if not all(isfinite(x) for x in (entry, stop, gross)) or not 0 < stop < entry:
                continue
        except (KeyError, TypeError, ValueError):
            continue
        net = gross - cost_pct
        net_returns.append(net)
        net_rs.append(net / ((entry - stop) / entry * 100))
    gains = sum(x for x in net_returns if x > 0)
    losses = -sum(x for x in net_returns if x < 0)
    return {
        "sample_count": len(net_returns), "unmodeled_count": len(rows) - len(net_returns),
        "net_modeled_positive": sum(x > 0 for x in net_returns),
        "net_modeled_negative": sum(x < 0 for x in net_returns),
        "net_modeled_flat": sum(x == 0 for x in net_returns),
        "mean_net_modeled_return_pct": round(mean(net_returns), 4) if net_returns else None,
        "mean_net_modeled_r": round(mean(net_rs), 4) if net_rs else None,
        "net_modeled_profit_factor": round(gains / losses, 4) if losses else None,
        "assumed_round_trip_cost_pct": cost_pct,
        "definition": "종료된 50/50 계획의 비용 차감 모의 결과·실제 체결 또는 계좌 수익 아님",
    }
