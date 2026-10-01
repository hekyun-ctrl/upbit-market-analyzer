import pytest

from exit_plan import exit_plan_metrics, modeled_exit_return


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
