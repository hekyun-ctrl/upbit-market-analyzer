import pytest

from exit_plan import exit_plan_metrics, modeled_exit_return, partial_plan_summary


PLAN = {"mode": "partial_50_50", "target_1_fraction": 0.5,
        "runner_fraction": 0.5, "runner_ceiling": 105}


def test_partial_plan_counts_fifty_percent_at_each_real_target():
    result = exit_plan_metrics(100, 98, 103, 105, PLAN)
    assert result["first_target_risk_reward"] == 1.5
    assert result["risk_reward"] == 2
    # A 10% observation target cannot silently replace a 5% runner ceiling.
    with pytest.raises(ValueError):
        exit_plan_metrics(100, 98, 103, 110, PLAN)
    assert exit_plan_metrics(100, 98, 103, 110)["risk_reward"] == 1.5


@pytest.mark.parametrize("entry,stop,t1,t2", [
    (100, 100, 103, 105), (100, 98, 100, 105),
    (100, 98, 103, 102), (float("nan"), 98, 103, 105),
])
def test_invalid_partial_ordering_cannot_build_a_reward(entry, stop, t1, t2):
    with pytest.raises(ValueError):
        exit_plan_metrics(entry, stop, t1, t2, PLAN)


def test_modeled_path_distinguishes_partial_protection_and_full_stop():
    assert modeled_exit_return(100, 98, 103, 105, True, 100)["modeled_plan_return_pct"] == 1.5
    # Do not hide an observed stop gap behind the planned stop price.
    assert modeled_exit_return(100, 98, 103, 105, False, 97)["modeled_plan_r"] == -1.5
    # Upward jumps do not invent a better limit exit than the published plan.
    assert modeled_exit_return(100, 98, 103, 105, True, 110)["modeled_plan_r"] == 2


def test_partial_summary_cost_can_turn_small_gross_gain_into_a_loss():
    rows = [{"exit_plan": PLAN, "entry_price": 100, "stop_price": 98,
             "modeled_plan_return_pct": value} for value in (0.1, 4, -2)]
    report = partial_plan_summary(rows, 0.2)
    assert report["sample_count"] == 3
    assert report["net_modeled_positive"] == 1
    assert report["net_modeled_negative"] == 2
    assert report["mean_net_modeled_return_pct"] == 0.5
    assert report["mean_net_modeled_r"] == 0.25
    assert report["net_modeled_profit_factor"] == round(3.8 / 2.3, 4)


def test_partial_summary_does_not_invent_returns_for_missing_or_invalid_data():
    assert partial_plan_summary([])["mean_net_modeled_return_pct"] is None
    report = partial_plan_summary([
        {"exit_plan": PLAN, "entry_price": 100, "stop_price": 100,
         "modeled_plan_return_pct": 4},
        {"exit_plan": PLAN, "entry_price": 100, "stop_price": 98},
    ])
    assert report["sample_count"] == 0
    assert report["unmodeled_count"] == 2
    assert report["net_modeled_profit_factor"] is None
