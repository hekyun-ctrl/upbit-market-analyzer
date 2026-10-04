import pytest

from candidate_analysis import CandidateConfig, _trade_value_metrics
from monitor import MonitorConfig


def test_moderate_intraday_preleader_defaults(monkeypatch):
    for name in (
        "MONITOR_PRELEADER_MIN_VALUE_RATIO_10M",
        "MONITOR_PRELEADER_MIN_VALUE_RATIO_30M",
        "MONITOR_PRELEADER_MIN_3M_PCT",
        "MONITOR_PRELEADER_MIN_5M_PCT",
        "MONITOR_PRELEADER_MAX_PERCENTILE",
    ):
        monkeypatch.delenv(name, raising=False)

    config = MonitorConfig.from_env()

    assert config.preleader_min_value_ratio_10m == 1.5
    assert config.preleader_min_value_ratio_30m == 1.2
    assert config.preleader_min_3m_pct == 0.4
    assert config.preleader_min_5m_pct == 0.6
    assert config.preleader_max_percentile == 20.0


def test_fast_candidate_uses_broader_rank_and_multi_window_volume_defaults(monkeypatch):
    for name in (
        "CANDIDATE_FAST_LEADER_MAX_PERCENTILE",
        "CANDIDATE_FAST_LEADER_MIN_VALUE_RATIO_10M",
        "CANDIDATE_FAST_LEADER_MIN_VALUE_RATIO_30M",
    ):
        monkeypatch.delenv(name, raising=False)

    config = CandidateConfig.from_env()

    assert config.fast_leader_max_percentile == 10.0
    assert config.fast_leader_min_value_ratio_10m == 1.5
    assert config.fast_leader_min_value_ratio_30m == 1.2


def test_completed_turnover_ratios_use_krw_trade_value():
    candles = [
        {"candle_acc_trade_price": 150.0 - index} for index in range(21)
    ]

    latest_vs_baseline, latest_vs_previous = _trade_value_metrics(candles)

    assert latest_vs_baseline == 150.0 / 139.5
    assert latest_vs_previous == 150.0 / 149.0
