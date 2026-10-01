from datetime import datetime, timedelta, timezone

import candidate_analysis
import pytest
from candidate_analysis import (
    CandidateConfig,
    _completed,
    _completed_after_signal,
    _double_bollinger_context,
    _resistances,
    evaluate_candidate,
    validate_candidate_survival,
    validate_btc_survival,
)

def _config(**overrides):
    values = {
        "enabled": True,
        "confirm_seconds": 30,
        "survival_confirm_seconds": 60,
        "fast_leader_enabled": True,
        "fast_leader_confirm_seconds": 6,
        "fast_leader_max_percentile": 2.0,
        "fast_leader_min_value_ratio_10m": 5.0,
        "fast_leader_min_value_ratio_30m": 5.0,
        "fast_leader_max_extension_pct": 1.5,
        "persistent_leader_max_day_change_pct": 35.0,
        "persistent_leader_min_15m_pct": 3.0,
        "pullback_entry_only_enabled": True,
        "pullback_survival_confirm_seconds": 20,
        "pullback_watch_min_pct": 0.6,
        "pullback_watch_max_pct": 4.0,
        "pullback_watch_reclaim_pct": 0.35,
        "reentry_max_drawdown_from_day_high_pct": 7.0,
        "risk_off_exception_max_percentile": 1.0,
        "risk_off_exception_min_5m_pct": 2.0,
        "risk_off_fresh_leader_max_percentile": 5.0,
        "risk_off_fresh_leader_min_5m_pct": 1.5,
        "risk_off_low_room_score_cap": 89,
        "min_score": 80,
        "cooldown_seconds": 900,
        "repeat_cooldown_seconds": 14400,
        "valid_seconds": 300,
        "max_day_change_pct": 20.0,
        "max_rsi_1m": 78.0,
        "max_rsi_5m": 75.0,
        "hard_max_rsi_1m": 88.0,
        "hard_max_rsi_5m": 85.0,
        "elite_retest_max_rsi_5m": 92.0,
        "min_orderbook_ratio": 0.8,
        "hard_min_orderbook_ratio": 0.4,
        "max_price_extension_pct": 2.5,
        "min_completed_volume_ratio": 1.2,
        "min_volume_vs_previous": 0.65,
        "min_close_position": 0.6,
        "max_upper_wick_ratio": 0.4,
        "max_spread_pct": 0.5,
        "hard_max_spread_pct": 1.2,
        "min_trade_value_24h_krw": 1_000_000_000,
        "min_resistance_room_pct": 5.0,
        "hard_min_resistance_room_pct": 2.5,
        "min_risk_reward": 2.0,
        "max_stop_loss_pct": 3.0,
        "max_btc_decline_pct": -1.5,
        "hard_min_market_breadth_pct": 35.0,
        "btc_weak_min_orderbook_ratio": 1.0,
        "hot_rsi_1m": 75.0,
        "orderbook_sample_count": 3,
        "orderbook_sample_interval_seconds": 2.0,
        "reentry_window_seconds": 1800,
        "reentry_cooldown_seconds": 300,
        "watchlist_window_seconds": 43200,
        "raw_signal_target_pct": 5.0,
        "raw_signal_stop_pct": 3.0,
        "btc_weak_score_penalty": 10,
        "day_overheat_score_penalty": 8,
        "orderbook_score_penalty": 4,
        "spread_score_penalty": 4,
        "resistance_score_penalty": 6,
        "rsi_score_penalty": 4,
        "double_bb_enabled": False,
        "double_bb_require_confirmation": True,
        "double_bb_lookback": 4,
        "double_bb_score_bonus": 6,
        "double_bb_unconfirmed_penalty": 6,
        "double_bb_reversal_wick_ratio": 0.35,
        "availability_balance_enabled": True,
        "availability_max_soft_warnings": 2,
        "availability_soft_penalty": 4,
        "availability_min_volume_ratio": 0.85,
        "availability_min_volume_vs_previous": 0.45,
        "availability_min_close_position": 0.35,
        "availability_max_upper_wick_ratio": 0.65,
        "availability_resistance_floor_pct": 1.5,
        "availability_min_score": 84,
        "relative_strength_required": True,
        "relative_strength_top_percent": 15.0,
        "relative_strength_min_5m_pct": 0.8,
        "relative_strength_score_bonus": 8,
        "early_trend_required": True,
        "early_leader_lane_enabled": True,
        "early_leader_max_percentile": 5.0,
        "early_leader_score_bonus": 8,
        "early_leader_resistance_floor_pct": 1.0,
        "breakout_resistance_cluster_pct": 1.5,
        "require_first_retest": True,
        "retest_tolerance_pct": 0.8,
        "leader_watch_enabled": True,
        "leader_watch_max_rechecks": 3,
        "leader_pullback_min_pct": 1.0,
        "leader_pullback_max_pct": 5.0,
        "leader_reclaim_pct": 0.5,
        "leader_recheck_cooldown_seconds": 300,
        "trend_target_2_pct": 8.0,
        "trend_target_3_pct": 15.0,
        "trend_target_4_pct": 20.0,
        "trend_tracking_seconds": 21600,
        "outcome_tracking_seconds": 7200,
    }
    values.update(overrides)
    return CandidateConfig(**values)


def _candles(count=120, monotonic=False):
    closes = []
    price = 100.0
    pattern = (0.1, -0.1, 0.1, -0.1, 0.2)
    for index in range(count):
        price += 0.15 if monotonic else pattern[index % len(pattern)]
        closes.append(round(price, 4))
    result = []
    for index, close in enumerate(closes):
        volume = 100.0
        if index == count - 2:
            volume = 300.0
        result.append(
            {
                "opening_price": close - 0.06,
                "high_price": close + 0.02,
                "low_price": close - 0.1,
                "trade_price": close,
                "candle_acc_trade_volume": volume,
                "candle_acc_trade_price": close * volume,
            }
        )
    return list(reversed(result))


def _orderbook(price, bid_ratio=1.5):
    return {
        "total_ask_size": 1_000.0,
        "total_bid_size": 1_000.0 * bid_ratio,
        "orderbook_units": [
            {
                "bid_price": round(price - index * 0.1, 1),
                "ask_price": round(price + 0.1 + index * 0.1, 1),
                "bid_size": 100.0,
                "ask_size": 100.0,
            }
            for index in range(15)
        ],
    }


def _with_dates(candles, minutes):
    now = datetime.now(timezone.utc)
    start = now.replace(second=0, microsecond=0)
    start -= timedelta(minutes=int(start.timestamp() // 60) % minutes)
    return [
        {
            **c,
            "candle_date_time_utc": (
                start - timedelta(minutes=minutes * i)
            ).isoformat(),
        }
        for i, c in enumerate(candles)
    ]


def _hourly_setup():
    one, five = _candles(), _candles()
    current = float(one[0]["trade_price"])
    alert = {
        "market": "KRW-TEST",
        "signal": "breakout",
        "price": current,
        "breakout_level": current * 0.995,
        "relative_strength_ready": True,
        "relative_strength_eligible": True,
        "relative_strength_percentile": 3.0,
        "momentum_5m_pct": 1.5,
        "momentum_15m_pct": 2.0,
        "early_trend": False,
        "market_regime": "neutral",
        "market_breadth_5m_pct": 50.0,
        "pullback_retest": True,
    }
    ticker = {
        "trade_price": current,
        "signed_change_rate": 0.05,
        "high_price": current * 1.01,
        "acc_trade_price_24h": 10_000_000_000,
    }
    return alert, ticker, one, five


def _sustained_setup(monkeypatch):
    alert, ticker, one, five = _hourly_setup()
    alert.update(
        hourly_recheck=True,
        relative_strength_eligible=False,
        momentum_5m_pct=0.4,
        momentum_60m_pct=3.0,
    )
    monkeypatch.setattr(
        candidate_analysis,
        "_double_bollinger_context",
        lambda *args, **kwargs: {
            "ready": True,
            "confirmed": True,
            "fake_breakout": False,
            "status": "재지지",
        },
    )
    return (
        alert,
        ticker,
        _with_dates(one, 1),
        _with_dates(five, 5),
        _with_dates(_candles(), 15),
        _with_dates(_candles(monotonic=True), 60),
    )


def test_sustained_retest_uses_hourly_trend_when_short_impulse_is_below_old_gate(
    monkeypatch,
):
    alert, ticker, one, five, fifteen, hourly = _sustained_setup(monkeypatch)
    candidate, reasons = evaluate_candidate(
        alert,
        ticker,
        _orderbook(ticker["trade_price"]),
        one,
        five,
        _config(min_score=90, double_bb_enabled=True),
        candles_15m=fifteen,
        candles_60m=hourly,
    )
    assert reasons == []
    assert candidate["sustained_retest"]
    assert candidate["holding_mode"] == "hourly_structure"
    assert candidate["completed_5m_volume_ratio"] >= 1.5
    assert candidate["completed_15m_volume_ratio"] >= 1.0
    assert candidate["score"] >= 90


def test_hourly_partial_exit_admits_plan_without_inventing_ten_percent_room(monkeypatch):
    alert, ticker, one, five, fifteen, hourly = _sustained_setup(monkeypatch)
    monkeypatch.setattr(candidate_analysis, "_atr", lambda bars: 2.3)
    candidate, reasons = evaluate_candidate(
        alert, ticker, _orderbook(ticker["trade_price"]), one, five,
        _config(min_score=90, double_bb_enabled=True),
        candles_15m=fifteen, candles_60m=hourly,
    )
    assert reasons == []
    assert 1 <= candidate["first_target_risk_reward"] < 2
    assert candidate["risk_reward"] >= 2
    assert candidate["exit_plan"]["mode"] == "partial_50_50"
    assert candidate["target_2_pct"] <= 5
    assert candidate["target_2"] <= candidate["resistance_price"]
    assert candidate["exit_plan"]["ceiling_basis"] == "conservative_projection"


def test_snapshot_crossing_a_minute_boundary_cannot_reclassify_partial_bars():
    before = datetime(2026, 10, 1, 0, 34, 59, tzinfo=timezone.utc)
    candles = [
        {"candle_date_time_utc": f"2026-10-01T00:{minute}:00", "trade_price": minute}
        for minute in (35, 34, 33, 32)
    ]
    # Data fetched after 00:35 may include both 00:35 and the still-open-at-
    # capture-time 00:34. Neither can become a completed decision candle.
    assert [c["trade_price"] for c in _completed(candles, 1, before)] == [33, 32]


def test_ten_minute_no_gap_rule_does_not_silently_require_twenty_one():
    from trend_context import recent_minute_candles_contiguous
    before = datetime(2026, 10, 1, 0, 35, 5, tzinfo=timezone.utc)
    candles = [{"candle_date_time_utc": (before.replace(second=0) - timedelta(minutes=i)).isoformat()}
               for i in range(1, 25) if i != 15]
    assert recent_minute_candles_contiguous(candles, before)
    candles.pop(4)
    assert not recent_minute_candles_contiguous(candles, before)


@pytest.mark.parametrize("cause", ["wide_risk", "near_resistance", "weak_book"])
def test_partial_exit_does_not_rescue_unavailable_reward_or_weak_liquidity(monkeypatch, cause):
    alert, ticker, one, five, fifteen, hourly = _sustained_setup(monkeypatch)
    monkeypatch.setattr(candidate_analysis, "_atr", lambda bars: 3.5 if cause == "wide_risk" else 2.3)
    book = _orderbook(ticker["trade_price"])
    if cause == "near_resistance":
        monkeypatch.setattr(candidate_analysis, "_resistances", lambda *args: [ticker["trade_price"] * 1.032])
    if cause == "weak_book":
        book["total_bid_size"] = 10
        for level in book["orderbook_units"]:
            level["bid_size"] = 0.01
    candidate, reasons = evaluate_candidate(
        alert, ticker, book, one, five, _config(min_score=90, double_bb_enabled=True),
        candles_15m=fifteen, candles_60m=hourly,
    )
    assert candidate is None
    assert reasons
    if cause != "weak_book":
        assert "손익비 부족" in reasons[0]
        assert alert["risk_plan_diagnostics"]["risk_reward"] < 2
    else:
        assert any("호가" in reason or "손익비 부족" in reason for reason in reasons)
        assert alert.get("risk_plan_diagnostics", {}).get("exit_plan") is None


def test_extra_bonuses_do_not_erase_explicit_risk_deductions(monkeypatch):
    alert, ticker, one, five, fifteen, hourly = _sustained_setup(monkeypatch)
    alert.update(
        relative_strength_eligible=True,
        early_trend=True,
        change_1m_pct=2.5,
        volume_ratio_vs_previous_1m=6,
    )
    candidate, reasons = evaluate_candidate(
        alert,
        ticker,
        _orderbook(ticker["trade_price"], bid_ratio=0.5),
        one,
        five,
        _config(min_score=90, double_bb_enabled=True),
        candles_15m=fifteen,
        candles_60m=hourly,
    )
    assert reasons == []
    assert candidate["score_before_penalties"] == 100
    assert candidate["score_penalty"] >= 4
    assert candidate["score"] == 100 - candidate["score_penalty"]
    assert candidate["score"] < 100


@pytest.mark.parametrize(
    "failure",
    [
        "hourly_missing",
        "five_volume",
        "fifteen_volume",
        "minute_gap",
        "five_gap",
        "wb_missing",
        "rank_lost",
    ],
)
def test_sustained_retest_cannot_bypass_completed_data_volume_or_wb(
    monkeypatch, failure
):
    alert, ticker, one, five, fifteen, hourly = _sustained_setup(monkeypatch)
    if failure == "hourly_missing":
        hourly = []
    elif failure == "five_volume":
        five[1]["candle_acc_trade_volume"] = 100
        five[0]["candle_acc_trade_volume"] = 1_000_000  # unfinished surge is irrelevant
    elif failure == "fifteen_volume":
        fifteen[1]["candle_acc_trade_volume"] = 50
    elif failure == "minute_gap":
        one.pop(5)
    elif failure == "five_gap":
        five.pop(5)
    elif failure == "wb_missing":
        monkeypatch.setattr(
            candidate_analysis,
            "_double_bollinger_context",
            lambda *args, **kwargs: {"ready": True, "confirmed": False},
        )
    else:
        alert["relative_strength_percentile"] = 8.0
    candidate, reasons = evaluate_candidate(
        alert,
        ticker,
        _orderbook(ticker["trade_price"]),
        one,
        five,
        _config(min_score=90, double_bb_enabled=True),
        candles_15m=fifteen,
        candles_60m=hourly,
    )
    assert candidate is None
    assert reasons


def test_btc_survival_ignores_unfinished_extreme_bar_but_checks_new_completed_crash():
    bars = _with_dates(_candles(), 5)
    bars[0]["trade_price"] = 1  # in-progress bar must not enter indicator judgement
    ticker = {"trade_price": bars[1]["trade_price"]}
    metrics, reasons = validate_btc_survival(ticker, bars, _config())
    assert reasons == []
    assert metrics["survival_btc_5m_pct"] > -0.8
    bars[1]["opening_price"] = bars[1]["trade_price"] / 0.98
    assert (
        "생존 중 BTC 완료봉 급락" in validate_btc_survival(ticker, bars, _config())[1]
    )


def test_btc_survival_blocks_live_crash_and_stale_or_sparse_confirmation():
    bars = _with_dates(_candles(), 5)
    ticker = {"trade_price": bars[1]["trade_price"] * 0.98}
    assert (
        "생존 중 BTC 실시간 급락 보호"
        in validate_btc_survival(ticker, bars, _config())[1]
    )
    ticker["trade_price"] = bars[1]["trade_price"]
    assert validate_btc_survival(ticker, bars[5:], _config())[1]
    bars.pop(5)
    assert validate_btc_survival(ticker, bars, _config())[1]


def test_hourly_retest_can_hold_trend_after_early_acceleration_has_cooled():
    alert, ticker, one, five = _hourly_setup()
    candidate, rejected = evaluate_candidate(
        alert,
        ticker,
        _orderbook(ticker["trade_price"]),
        one,
        five,
        _config(),
        candles_15m=_with_dates(_candles(), 15),
        candles_60m=_with_dates(_candles(monotonic=True), 60),
        candles_240m=_with_dates(list(reversed(_candles())), 240),
    )
    assert rejected == []
    assert candidate["holding_mode"] == "hourly_structure"
    assert candidate["stop_timeframe"] == "5분 구조"
    assert candidate["target_4"] is not None
    assert 0 < candidate["suggested_position_pct"] <= 5
    assert candidate["higher_timeframe_context"]["four_hour"]["status"] == "하락 우위"


def test_missing_hourly_data_does_not_invent_a_longer_holding_exception():
    alert, ticker, one, five = _hourly_setup()
    candidate, rejected = evaluate_candidate(
        alert, ticker, _orderbook(ticker["trade_price"]), one, five, _config()
    )
    assert candidate is None
    assert "상승 초기 가속 구간 아님" in rejected
    alert["early_trend"] = True
    candidate, rejected = evaluate_candidate(
        alert, ticker, _orderbook(ticker["trade_price"]), one, five, _config()
    )
    assert rejected == []
    assert candidate["holding_mode"] == "initial_signal"


def test_hourly_trend_does_not_relax_entry_for_an_ineligible_relative_leader():
    alert, ticker, one, five = _hourly_setup()
    alert["relative_strength_eligible"] = False
    candidate, rejected = evaluate_candidate(
        alert,
        ticker,
        _orderbook(ticker["trade_price"]),
        one,
        five,
        _config(),
        candles_15m=_with_dates(_candles(), 15),
        candles_60m=_with_dates(_candles(monotonic=True), 60),
    )
    assert candidate is None
    assert "상승 초기 가속 구간 아님" in rejected


def test_hourly_stop_too_wide_is_rejected_instead_of_clipped_inside_support():
    alert, ticker, one, five = _hourly_setup()
    five[1]["low_price"] = ticker["trade_price"] * 0.9
    candidate, rejected = evaluate_candidate(
        alert,
        ticker,
        _orderbook(ticker["trade_price"]),
        one,
        five,
        _config(),
        candles_15m=_with_dates(_candles(), 15),
        candles_60m=_with_dates(_candles(monotonic=True), 60),
    )
    assert candidate is None
    assert any("5분 구조 손절폭" in reason for reason in rejected)


def test_btc_completed_bar_crash_cannot_be_rescued_by_strong_book():
    alert, ticker, one, five = _hourly_setup()
    alert["early_trend"] = True
    btc = _candles()
    btc[1]["opening_price"] = btc[1]["trade_price"] / 0.98
    candidate, rejected = evaluate_candidate(
        alert,
        ticker,
        _orderbook(ticker["trade_price"], bid_ratio=3),
        one,
        five,
        _config(),
        btc_candles_5m=btc,
    )
    assert candidate is None
    assert any("BTC 완료봉 급락" in reason for reason in rejected)


def test_btc_slow_ma_decline_is_caution_not_a_crash():
    alert, ticker, one, five = _hourly_setup()
    alert["early_trend"] = True
    btc = _candles(monotonic=True)
    for c in btc:
        c["trade_price"] = 200 - c["trade_price"]
        c["opening_price"] = c["trade_price"] + 0.01
        c["high_price"] = c["opening_price"] + 0.01
        c["low_price"] = c["trade_price"] - 0.01
    candidate, rejected = evaluate_candidate(
        alert,
        ticker,
        _orderbook(ticker["trade_price"]),
        one,
        five,
        _config(min_score=75),
        btc_candles_5m=btc,
        btc_candles_15m=btc,
    )
    assert rejected == []
    assert candidate["btc_caution"]
    assert not candidate["btc_crash"] and not candidate["btc_weak"]
    assert candidate["score"] <= 100 - _config().btc_weak_score_penalty // 2
    assert any("급락 아님" in note for note in candidate["risk_notes"])


def _wb_breakout_candles(retest=False, fakeout=False):
    chronological = []
    for index in range(30):
        close = 100.0 + (index % 3) * 0.03
        opening = close - 0.02
        high = close + 0.08
        low = close - 0.08
        if index == 28 and (retest or fakeout):
            opening = 100.0
            close = 106.0
            high = 106.2
            low = 99.9
        if index == 29 and retest:
            opening = 106.0
            close = 105.8
            high = 106.1
            low = 105.4
        elif index == 29 and fakeout:
            opening = 100.1
            close = 100.0
            high = 106.0
            low = 99.9
        elif index == 29:
            opening = 100.0
            close = 106.0
            high = 106.2
            low = 99.9
        chronological.append(
            {
                "opening_price": opening,
                "high_price": high,
                "low_price": low,
                "trade_price": close,
                "candle_acc_trade_volume": 100.0,
                "candle_acc_trade_price": close * 100.0,
            }
        )
    return list(reversed(chronological))


def test_double_bollinger_confirms_true_breakout_and_first_retest():
    breakout = _double_bollinger_context(
        _wb_breakout_candles(), 105.0, lookback=4
    )
    assert breakout["confirmed"] is True
    assert breakout["true_breakout"] is True
    assert "동시 돌파" in breakout["status"]

    retest = _double_bollinger_context(
        _wb_breakout_candles(retest=True), 105.5, lookback=4
    )
    assert retest["confirmed"] is True
    assert retest["first_retest"] is True
    assert "첫 눌림" in retest["status"]


def test_double_bollinger_rejects_upper_wick_fakeout():
    result = _double_bollinger_context(
        _wb_breakout_candles(fakeout=True), 105.0, lookback=4
    )
    assert result["confirmed"] is False
    assert result["fake_breakout"] is True
    assert "가짜 돌파" in result["status"]


def test_healthy_signal_builds_orderable_risk_plan():
    one = _candles()
    five = _candles()
    current = float(one[0]["trade_price"])
    alert = {
        "time_utc": "2026-09-12T10:00:00+00:00",
        "market": "KRW-TEST",
        "signal": "breakout",
        "price": current,
    }
    ticker = {
        "trade_price": current,
        "signed_change_rate": 0.08,
        "high_price": current * 1.08,
    }

    candidate, rejected = evaluate_candidate(
        alert,
        ticker,
        _orderbook(current),
        one,
        five,
        _config(),
    )

    assert rejected == []
    assert candidate is not None
    assert candidate["score"] >= 80
    assert candidate["entry_low"] <= candidate["entry_high"]
    assert candidate["stop_price"] < candidate["entry_low"]
    assert candidate["target_1"] > candidate["entry_high"]
    assert candidate["target_2"] > candidate["target_1"]
    assert candidate["target_mode"] in {
        "균형 위험비형",
        "강한 추세 확장형",
        "상대강도 추세추적형",
    }
    assert candidate["resistance_room_pct"] >= 5.0
    assert candidate["risk_reward"] >= 2.0
    expected_rr = (candidate["target_1"] - candidate["current_price"]) / (
        candidate["current_price"] - candidate["stop_price"]
    )
    assert candidate["risk_reward"] == round(expected_rr, 2)
    assert candidate["risk_reward_reference_price"] == candidate["target_1"]


def test_price_surge_uses_structural_resistance_targets():
    one = _candles()
    five = _candles()
    current = float(one[0]["trade_price"])
    alert = {
        "time_utc": "2026-09-12T10:00:00+00:00",
        "market": "KRW-TEST",
        "signal": "price_volume_surge",
        "price": current,
    }
    ticker = {
        "trade_price": current,
        "signed_change_rate": 0.08,
        "high_price": current * 1.08,
    }

    candidate, rejected = evaluate_candidate(
        alert,
        ticker,
        _orderbook(current),
        one,
        five,
        _config(),
    )

    assert rejected == []
    assert candidate is not None
    assert candidate["target_mode"] == "균형 위험비형"


def test_consolidation_rebreakout_is_accepted_and_explained():
    one = _candles()
    five = _candles()
    current = float(one[0]["trade_price"])
    alert = {
        "time_utc": "2026-09-12T10:00:00+00:00",
        "market": "KRW-TEST",
        "signal": "consolidation_rebreakout",
        "price": current,
        "consolidation_minutes": 30,
    }
    ticker = {
        "trade_price": current,
        "signed_change_rate": 0.08,
        "high_price": current * 1.08,
    }

    candidate, rejected = evaluate_candidate(
        alert, ticker, _orderbook(current), one, five, _config()
    )

    assert rejected == []
    assert candidate is not None
    assert candidate["source_signal"] == "consolidation_rebreakout"
    assert "30분 횡보 상단 재돌파" in candidate["reasons"]
    assert candidate["target_mode"] == "강한 추세 확장형"
    assert 6.8 <= candidate["target_1_pct"] <= 7.1
    assert 9.8 <= candidate["target_2_pct"] <= 10.1


def test_internal_preleader_can_pass_later_pullback_recheck():
    one = _candles()
    five = _candles()
    current = float(one[0]["trade_price"])
    candidate, rejected = evaluate_candidate(
        {
            "market": "KRW-TEST",
            "signal": "leader_volume_acceleration",
            "price": current,
            "is_reentry": True,
            "pullback_retest": True,
        },
        {
            "trade_price": current,
            "signed_change_rate": 0.08,
            "high_price": current * 1.06,
        },
        _orderbook(current),
        one,
        five,
        _config(min_score=80),
    )

    assert rejected == []
    assert candidate is not None
    assert candidate["source_signal"] == "leader_volume_acceleration"
    assert "09시 전후 거래대금 선행 가속에서 조기 포착" in candidate["reasons"]


def test_persistent_leader_can_pass_after_early_trend_phase():
    one = _candles()
    five = _candles()
    current = float(one[0]["trade_price"])
    candidate, rejected = evaluate_candidate(
        {
            "market": "KRW-TEST",
            "signal": "persistent_leader_acceleration",
            "persistent_leader": True,
            "price": current,
            "relative_strength_ready": True,
            "relative_strength_eligible": True,
            "relative_strength_rank": 2,
            "relative_strength_universe": 100,
            "relative_strength_percentile": 2.0,
            "momentum_5m_pct": 1.2,
            "momentum_15m_pct": 5.0,
            "momentum_60m_pct": 14.0,
            "early_trend": False,
        },
        {
            "trade_price": current,
            "signed_change_rate": 0.26,
            "high_price": current * 1.08,
        },
        _orderbook(current),
        one,
        five,
        _config(min_score=80),
    )

    assert rejected == []
    assert candidate is not None
    assert candidate["source_signal"] == "persistent_leader_acceleration"
    assert candidate["day_overheated"] is False
    assert "지속형 선도주 재가속 경로 통과" in candidate["reasons"]


def test_ready_relative_strength_rejects_non_leader():
    one = _candles()
    five = _candles()
    current = float(one[0]["trade_price"])
    alert = {
        "market": "KRW-TEST",
        "signal": "breakout",
        "price": current,
        "relative_strength_ready": True,
        "relative_strength_eligible": False,
        "relative_strength_rank": 80,
        "relative_strength_universe": 100,
        "relative_strength_percentile": 80.0,
        "momentum_5m_pct": 0.2,
        "momentum_15m_pct": -0.1,
    }
    ticker = {
        "trade_price": current,
        "signed_change_rate": 0.08,
        "high_price": current * 1.08,
    }

    candidate, rejected = evaluate_candidate(
        alert, ticker, _orderbook(current), one, five, _config()
    )

    assert candidate is None
    assert any("상대강도 상위권 아님" in reason for reason in rejected)


def test_top_rank_with_fading_momentum_is_not_mislabeled_as_low_rank():
    one = _candles()
    five = _candles()
    current = float(one[0]["trade_price"])
    candidate, rejected = evaluate_candidate(
        {
            "market": "KRW-TEST",
            "signal": "breakout",
            "price": current,
            "relative_strength_ready": True,
            "relative_strength_eligible": False,
            "relative_strength_rank": 2,
            "relative_strength_universe": 200,
            "relative_strength_percentile": 1.0,
            "momentum_5m_pct": 0.4,
            "momentum_15m_pct": 1.2,
        },
        {"trade_price": current, "signed_change_rate": 0.08},
        _orderbook(current),
        one,
        five,
        _config(),
    )

    assert candidate is None
    assert any("5분 상대 모멘텀 부족" in reason for reason in rejected)
    assert not any("상대강도 상위권 아님" in reason for reason in rejected)


def test_relative_strength_retest_uses_trend_tracking_targets():
    one = _candles()
    five = _candles()
    current = float(one[0]["trade_price"])
    alert = {
        "market": "KRW-TEST",
        "signal": "breakout",
        "price": current,
        "breakout_level": current * 0.997,
        "relative_strength_ready": True,
        "relative_strength_eligible": True,
        "relative_strength_rank": 3,
        "relative_strength_universe": 100,
        "relative_strength_percentile": 3.0,
        "momentum_5m_pct": 2.0,
        "momentum_15m_pct": 4.0,
        "momentum_60m_pct": 6.0,
        "early_trend": True,
    }
    ticker = {
        "trade_price": current,
        "signed_change_rate": 0.08,
        "high_price": current * 1.08,
    }

    candidate, rejected = evaluate_candidate(
        alert, ticker, _orderbook(current), one, five, _config(min_score=80)
    )

    assert rejected == []
    assert candidate is not None
    assert candidate["target_mode"] == "상대강도 추세추적형"
    assert 7.8 <= candidate["target_2_pct"] <= 8.2
    assert candidate["first_retest_confirmed"] is True
    assert any("상대강도 3/100위" in reason for reason in candidate["reasons"])


def test_default_candidate_config_is_accuracy_first(monkeypatch):
    for name in (
        "CANDIDATE_AVAILABILITY_BALANCE_ENABLED",
        "CANDIDATE_AVAILABILITY_MIN_SCORE",
        "CANDIDATE_RELATIVE_STRENGTH_TOP_PERCENT",
        "CANDIDATE_RELATIVE_STRENGTH_MIN_5M_PCT",
        "CANDIDATE_EARLY_TREND_REQUIRED",
        "CANDIDATE_LEADER_WATCH_ENABLED",
        "CANDIDATE_LEADER_PULLBACK_MIN_PCT",
        "CANDIDATE_LEADER_PULLBACK_MAX_PCT",
        "CANDIDATE_LEADER_RECLAIM_PCT",
        "CANDIDATE_TREND_TARGET_2_PCT",
        "CANDIDATE_SURVIVAL_CONFIRM_SECONDS",
        "CANDIDATE_FAST_LEADER_ENABLED",
        "CANDIDATE_FAST_LEADER_CONFIRM_SECONDS",
        "CANDIDATE_FAST_LEADER_MAX_PERCENTILE",
        "CANDIDATE_FAST_LEADER_MIN_VALUE_RATIO_10M",
        "CANDIDATE_FAST_LEADER_MIN_VALUE_RATIO_30M",
        "CANDIDATE_FAST_LEADER_MAX_EXTENSION_PCT",
        "CANDIDATE_REPEAT_COOLDOWN_SECONDS",
        "CANDIDATE_HARD_MIN_MARKET_BREADTH_PCT",
        "CANDIDATE_REENTRY_MAX_DRAWDOWN_FROM_DAY_HIGH_PCT",
        "CANDIDATE_RISK_OFF_EXCEPTION_MAX_PERCENTILE",
        "CANDIDATE_RISK_OFF_EXCEPTION_MIN_5M_PCT",
        "CANDIDATE_RISK_OFF_FRESH_LEADER_MAX_PERCENTILE",
        "CANDIDATE_RISK_OFF_FRESH_LEADER_MIN_5M_PCT",
        "CANDIDATE_RISK_OFF_LOW_ROOM_SCORE_CAP",
        "CANDIDATE_ELITE_RETEST_MAX_RSI_5M",
        "CANDIDATE_OUTCOME_TRACKING_SECONDS",
    ):
        monkeypatch.delenv(name, raising=False)

    config = CandidateConfig.from_env()

    assert config.availability_balance_enabled is True
    assert config.availability_min_score == 86
    assert config.relative_strength_top_percent == 10.0
    assert config.relative_strength_min_5m_pct == 1.0
    assert config.early_trend_required is True
    assert config.leader_watch_enabled is True
    assert config.leader_pullback_min_pct == 1.0
    assert config.leader_pullback_max_pct == 5.0
    assert config.leader_reclaim_pct == 0.5
    assert config.trend_target_2_pct == 10.0
    assert config.survival_confirm_seconds == 60
    assert config.fast_leader_enabled is True
    assert config.fast_leader_confirm_seconds == 6
    assert config.fast_leader_max_percentile == 2.0
    assert config.fast_leader_min_value_ratio_10m == 5.0
    assert config.fast_leader_min_value_ratio_30m == 5.0
    assert config.fast_leader_max_extension_pct == 1.5
    assert config.repeat_cooldown_seconds == 14400
    assert config.hard_min_market_breadth_pct == 35.0
    assert config.reentry_max_drawdown_from_day_high_pct == 7.0
    assert config.risk_off_exception_max_percentile == 1.0
    assert config.risk_off_exception_min_5m_pct == 2.0
    assert config.risk_off_fresh_leader_max_percentile == 5.0
    assert config.risk_off_fresh_leader_min_5m_pct == 1.5
    assert config.risk_off_low_room_score_cap == 89
    assert config.elite_retest_max_rsi_5m == 92.0
    assert config.outcome_tracking_seconds == 7200


def test_rebreakout_reason_never_displays_zero_minutes():
    one = _candles()
    five = _candles()
    current = float(one[0]["trade_price"])
    candidate, rejected = evaluate_candidate(
        {
            "market": "KRW-TEST",
            "signal": "consolidation_rebreakout",
            "price": current,
            "is_reentry": True,
        },
        {
            "trade_price": current,
            "signed_change_rate": 0.08,
            "high_price": current * 1.06,
        },
        _orderbook(current),
        one,
        five,
        _config(),
    )

    assert rejected == []
    assert candidate is not None
    assert "0분 횡보 상단 재돌파" not in candidate["reasons"]
    assert "횡보 상단 재돌파" in candidate["reasons"]


def test_overheated_signal_is_rejected():
    one = _candles(monotonic=True)
    five = _candles(monotonic=True)
    current = float(one[0]["trade_price"])
    alert = {
        "time_utc": "2026-09-12T10:00:00+00:00",
        "market": "KRW-TEST",
        "signal": "price_volume_surge",
        "price": current,
    }
    ticker = {
        "trade_price": current,
        "signed_change_rate": 0.12,
        "high_price": current * 1.08,
    }

    candidate, rejected = evaluate_candidate(
        alert, ticker, _orderbook(current), one, five, _config()
    )

    assert candidate is None
    assert any("RSI 과열" in reason for reason in rejected)


def test_extremely_weak_orderbook_is_rejected():
    one = _candles()
    five = _candles()
    current = float(one[0]["trade_price"])
    alert = {
        "time_utc": "2026-09-12T10:00:00+00:00",
        "market": "KRW-TEST",
        "signal": "breakout",
        "price": current,
    }
    ticker = {
        "trade_price": current,
        "signed_change_rate": 0.08,
        "high_price": current * 1.08,
        "acc_trade_price_24h": 10_000_000_000,
    }

    candidate, rejected = evaluate_candidate(
        alert,
        ticker,
        _orderbook(current, bid_ratio=0.2),
        one,
        five,
        _config(min_score=80),
    )

    assert candidate is None
    assert any("호가 지지 하드차단" in reason for reason in rejected)


def test_moderately_weak_orderbook_is_rejected():
    one = _candles()
    five = _candles()
    current = float(one[0]["trade_price"])
    ticker = {
        "trade_price": current,
        "signed_change_rate": 0.08,
        "high_price": current * 1.08,
        "acc_trade_price_24h": 10_000_000_000,
    }

    candidate, rejected = evaluate_candidate(
        {"market": "KRW-TEST", "signal": "breakout", "price": current},
        ticker,
        _orderbook(current, bid_ratio=0.6),
        one,
        five,
        _config(min_score=80),
    )

    assert candidate is None
    assert any("호가 지지 하드차단" in reason for reason in rejected)


def test_spread_above_preferred_limit_is_rejected():
    one = _candles()
    five = _candles()
    current = float(one[0]["trade_price"])
    ticker = {
        "trade_price": current,
        "signed_change_rate": 0.08,
        "high_price": current * 1.08,
        "acc_trade_price_24h": 10_000_000_000,
    }

    moderate = _orderbook(current)
    for index, unit in enumerate(moderate["orderbook_units"]):
        unit["bid_price"] = current - 0.4 - index * 0.1
        unit["ask_price"] = current + 0.4 + index * 0.1
    candidate, rejected = evaluate_candidate(
        {"market": "KRW-TEST", "signal": "breakout", "price": current},
        ticker,
        moderate,
        one,
        five,
        _config(min_score=80),
    )
    assert candidate is None
    assert any("호가 스프레드 허용치 초과" in reason for reason in rejected)

    extreme = _orderbook(current)
    for index, unit in enumerate(extreme["orderbook_units"]):
        unit["bid_price"] = current - 0.8 - index * 0.1
        unit["ask_price"] = current + 0.8 + index * 0.1
    candidate, rejected = evaluate_candidate(
        {"market": "KRW-TEST", "signal": "breakout", "price": current},
        ticker,
        extreme,
        one,
        five,
        _config(min_score=80),
    )
    assert candidate is None
    assert any("스프레드 극단적 과다" in reason for reason in rejected)


def test_in_progress_candle_is_excluded_from_indicators():
    one = _candles()
    five = _candles()
    current = float(one[1]["trade_price"])
    one[0].update(
        {
            "opening_price": current,
            "high_price": current * 1.5,
            "low_price": current,
            "trade_price": current * 1.5,
            "candle_acc_trade_volume": 1_000_000,
        }
    )
    alert = {
        "market": "KRW-TEST",
        "signal": "breakout",
        "price": current,
        "breakout_level": current * 0.999,
    }
    ticker = {
        "trade_price": current,
        "signed_change_rate": 0.08,
        "high_price": current * 1.08,
    }

    candidate, rejected = evaluate_candidate(
        alert,
        ticker,
        _orderbook(current),
        one,
        five,
        _config(
            min_resistance_room_pct=0.0,
            hard_min_resistance_room_pct=0.0,
            min_risk_reward=0.0,
        ),
    )

    assert rejected == []
    assert candidate is not None
    assert candidate["rsi_1m"] < 78


def test_completed_close_below_breakout_is_rejected():
    one = _candles()
    five = _candles()
    current = float(one[0]["trade_price"])
    breakout = current * 1.002
    ticker = {
        "trade_price": current,
        "signed_change_rate": 0.08,
        "high_price": current * 1.08,
    }
    candidate, rejected = evaluate_candidate(
        {
            "market": "KRW-TEST",
            "signal": "breakout",
            "price": current,
            "breakout_level": breakout,
        },
        ticker,
        _orderbook(current),
        one,
        five,
        _config(),
    )

    assert candidate is None
    assert any("돌파선 아래" in reason for reason in rejected)


def test_collapsing_completed_volume_is_rejected():
    one = _candles()
    five = _candles()
    current = float(one[0]["trade_price"])
    one[1]["candle_acc_trade_volume"] = 10.0
    ticker = {
        "trade_price": current,
        "signed_change_rate": 0.08,
        "high_price": current * 1.08,
    }
    candidate, rejected = evaluate_candidate(
        {"market": "KRW-TEST", "signal": "breakout", "price": current},
        ticker,
        _orderbook(current),
        one,
        five,
        _config(),
    )

    assert candidate is None
    assert any("거래량" in reason for reason in rejected)


def test_three_percent_resistance_room_is_scored_with_a_warning():
    one = _candles()
    five = _candles()
    current = float(one[0]["trade_price"])
    ticker = {
        "trade_price": current,
        "signed_change_rate": 0.08,
        "high_price": current * 1.03,
    }
    candidate, rejected = evaluate_candidate(
        {"market": "KRW-TEST", "signal": "breakout", "price": current},
        ticker,
        _orderbook(current),
        one,
        five,
        _config(),
    )

    assert rejected == []
    assert candidate is not None
    assert candidate["resistance_room_pct"] < 5.0
    assert candidate["target_1_pct"] <= 3.1
    assert candidate["target_2_pct"] >= 4.9
    assert any("저항 여유 제한" in note for note in candidate["risk_notes"])


def test_confirmed_resistance_room_below_hard_floor_is_rejected(monkeypatch):
    one = _candles()
    five = _candles()
    current = float(one[0]["trade_price"])
    ticker = {
        "trade_price": current,
        "signed_change_rate": 0.08,
        "high_price": current * 1.02,
    }
    monkeypatch.setattr(
        candidate_analysis, "_resistances", lambda *args: [current * 1.02]
    )
    candidate, rejected = evaluate_candidate(
        {"market": "KRW-TEST", "signal": "breakout", "price": current},
        ticker,
        _orderbook(current),
        one,
        five,
        _config(),
    )

    assert candidate is None
    assert any("저항까지 여유 부족" in reason for reason in rejected)


def test_orderbook_support_must_persist_to_pass():
    one = _candles()
    five = _candles()
    current = float(one[0]["trade_price"])
    ticker = {
        "trade_price": current,
        "signed_change_rate": 0.08,
        "high_price": current * 1.08,
        "acc_trade_price_24h": 10_000_000_000,
    }
    samples = [
        _orderbook(current, bid_ratio=1.2),
        _orderbook(current, bid_ratio=0.2),
        _orderbook(current, bid_ratio=0.2),
    ]
    candidate, rejected = evaluate_candidate(
        {"market": "KRW-TEST", "signal": "breakout", "price": current},
        ticker,
        samples[-1],
        one,
        five,
        _config(min_score=80),
        orderbook_samples=samples,
    )

    assert candidate is None
    assert any("호가 지지 하드차단" in reason for reason in rejected)


def test_moderate_rsi_overheat_is_penalized_and_extreme_is_rejected():
    one = _candles()
    five = _candles()
    current = float(one[0]["trade_price"])
    ticker = {
        "trade_price": current,
        "signed_change_rate": 0.08,
        "high_price": current * 1.08,
        "acc_trade_price_24h": 10_000_000_000,
    }
    alert = {"market": "KRW-TEST", "signal": "breakout", "price": current}

    candidate, rejected = evaluate_candidate(
        alert,
        ticker,
        _orderbook(current),
        one,
        five,
        _config(min_score=80, max_rsi_1m=50.0),
    )
    assert rejected == []
    assert candidate is not None
    assert any("RSI 주의" in note for note in candidate["risk_notes"])

    candidate, rejected = evaluate_candidate(
        alert,
        ticker,
        _orderbook(current),
        _candles(monotonic=True),
        _candles(monotonic=True),
        _config(min_score=80),
    )
    assert candidate is None
    assert any("RSI 과열" in reason for reason in rejected)


def test_elite_leader_retest_can_advance_despite_hot_5m_rsi_and_unconfirmed_wb(
    monkeypatch,
):
    one = _candles()
    five = _candles()
    five[1]["_force_high_rsi"] = True
    current = float(one[0]["trade_price"])
    original_analysis = candidate_analysis.analyze_candles

    def analysis_with_hot_five_minute_rsi(candles):
        result = original_analysis(candles)
        if candles and candles[0].get("_force_high_rsi"):
            result["rsi14"] = 90.0
        return result

    monkeypatch.setattr(
        candidate_analysis, "analyze_candles", analysis_with_hot_five_minute_rsi
    )
    monkeypatch.setattr(
        candidate_analysis,
        "_double_bollinger_context",
        lambda *args, **kwargs: {
            "ready": True,
            "confirmed": False,
            "fake_breakout": False,
            "status": "상단 미확정",
        },
    )
    monkeypatch.setattr(candidate_analysis, "_resistances", lambda *args: [])

    alert = {
        "time_utc": "2026-09-12T10:00:00+00:00",
        "market": "KRW-TEST",
        "signal": "breakout",
        "price": current,
        "breakout_level": current * 0.995,
        "is_reentry": True,
        "pullback_retest": True,
        "leader_pullback_recheck": True,
        "relative_strength_ready": True,
        "relative_strength_eligible": True,
        "relative_strength_rank": 1,
        "relative_strength_universe": 200,
        "relative_strength_percentile": 0.5,
        "best_relative_strength_percentile": 0.5,
        "momentum_5m_pct": 2.5,
        "momentum_15m_pct": 3.5,
        "early_trend": True,
        "market_regime": "neutral",
        "market_breadth_5m_pct": 50.0,
    }
    ticker = {
        "trade_price": current,
        "signed_change_rate": 0.08,
        "high_price": current * 1.01,
        "acc_trade_price_24h": 10_000_000_000,
    }

    candidate, rejected = evaluate_candidate(
        alert,
        ticker,
        _orderbook(current),
        one,
        five,
        _config(min_score=80, double_bb_enabled=True),
    )

    assert rejected == []
    assert candidate is not None
    assert any("최상위 선도주 첫 재지지 예외" in note for note in candidate["risk_notes"])
    assert any("RSI 주의" in note for note in candidate["risk_notes"])

    ordinary = dict(alert)
    ordinary.pop("leader_pullback_recheck")
    ordinary.pop("pullback_retest")
    candidate, rejected = evaluate_candidate(
        ordinary,
        ticker,
        _orderbook(current),
        one,
        five,
        _config(min_score=80, double_bb_enabled=True),
    )
    assert candidate is None
    assert any("RSI 과열" in reason for reason in rejected)
    assert any("WB 두 상단" in reason for reason in rejected)


def test_strong_price_volume_impulse_adds_six_points():
    one = _candles()
    five = _candles()
    current = float(one[0]["trade_price"])
    ticker = {
        "trade_price": current,
        "signed_change_rate": 0.08,
        "high_price": current * 1.08,
        "acc_trade_price_24h": 10_000_000_000,
    }
    base = {
        "market": "KRW-TEST",
        "signal": "price_volume_surge",
        "price": current,
    }
    plain, plain_rejected = evaluate_candidate(
        base,
        ticker,
        _orderbook(current),
        one,
        five,
        _config(min_score=50),
    )
    impulse, impulse_rejected = evaluate_candidate(
        {**base, "change_1m_pct": 2.5, "volume_ratio_vs_previous_1m": 6.0},
        ticker,
        _orderbook(current),
        one,
        five,
        _config(min_score=50),
    )

    assert plain_rejected == [] and impulse_rejected == []
    assert plain is not None and impulse is not None
    assert impulse["score"] == min(100, plain["score"] + 6)
    assert any("강한 가격·거래대금 유입" in reason for reason in impulse["reasons"])


def test_single_five_minute_micro_high_is_not_significant_resistance():
    current = 100.0
    chronological = [
        {"high_price": 99.0},
        {"high_price": 99.2},
        {"high_price": 101.0},
        {"high_price": 99.3},
        {"high_price": 99.1},
        {"high_price": 99.0},
    ]
    five = list(reversed(chronological))
    fifteen = [{"high_price": 99.0} for _ in range(8)]

    levels = _resistances(current, {"high_price": 108.0}, five, fifteen)

    assert levels == [108.0]


def test_fresh_nearby_day_high_is_not_treated_as_confirmed_resistance():
    current = 100.0
    flat = [{"high_price": 99.0} for _ in range(8)]

    levels = _resistances(current, {"high_price": 100.4}, flat, flat)

    assert levels == []


def test_no_confirmed_overhead_resistance_uses_conservative_expansion_reference(
    monkeypatch,
):
    one = _candles()
    five = _candles()
    current = float(one[0]["trade_price"])
    ticker = {
        "trade_price": current,
        "signed_change_rate": 0.08,
        "high_price": current * 1.004,
        "acc_trade_price_24h": 10_000_000_000,
    }
    alert = {"market": "KRW-TEST", "signal": "breakout", "price": current}
    monkeypatch.setattr(candidate_analysis, "_resistances", lambda *args: [])

    candidate, rejected = evaluate_candidate(
        alert,
        ticker,
        _orderbook(current),
        one,
        five,
        _config(min_score=80),
    )

    assert rejected == []
    assert candidate is not None
    assert candidate["resistance_confirmed"] is False
    assert candidate["target_2_pct"] >= 4.9
    assert any("상단 구조 저항 없음" in note for note in candidate["risk_notes"])


def test_completed_keeps_latest_candle_when_no_current_interval_trade_exists():
    now = datetime(2026, 9, 14, 0, 10, tzinfo=timezone.utc)
    stale = [
        {"candle_date_time_utc": (now - timedelta(minutes=2)).isoformat()},
        {"candle_date_time_utc": (now - timedelta(minutes=3)).isoformat()},
    ]
    active = [
        {"candle_date_time_utc": (now - timedelta(seconds=30)).isoformat()},
        {"candle_date_time_utc": (now - timedelta(minutes=1)).isoformat()},
    ]

    assert _completed(stale, 1, now) == stale
    assert _completed(active, 1, now) == active[1:]


def test_confirmation_requires_twenty_seconds_after_signal():
    candle = {"candle_date_time_utc": "2026-09-14T01:00:00+00:00"}

    assert not _completed_after_signal(candle, "2026-09-14T01:00:41+00:00")
    assert _completed_after_signal(candle, "2026-09-14T01:00:40+00:00")


def test_non_overheated_signal_can_use_one_mild_quality_warning():
    one = _candles()
    five = _candles()
    current = float(one[0]["trade_price"])
    one[1]["candle_acc_trade_volume"] = 115.0
    ticker = {
        "trade_price": current,
        "signed_change_rate": 0.04,
        "high_price": current * 1.08,
        "acc_trade_price_24h": 10_000_000_000,
    }

    candidate, rejected = evaluate_candidate(
        {"market": "KRW-TEST", "signal": "consolidation_rebreakout", "price": current},
        ticker,
        _orderbook(current),
        one,
        five,
        _config(min_score=80),
    )

    assert rejected == []
    assert candidate is not None
    assert candidate["availability_balanced"] is True
    assert any("거래량 다소 부족" in note for note in candidate["risk_notes"])


def test_safe_mid_80s_signal_becomes_limited_availability_candidate(monkeypatch):
    one = _candles()
    five = _candles()
    current = float(one[0]["trade_price"])
    one[1]["candle_acc_trade_volume"] = 115.0
    ticker = {
        "trade_price": current,
        "signed_change_rate": 0.04,
        "high_price": current * 1.08,
        "acc_trade_price_24h": 10_000_000_000,
    }
    monkeypatch.setattr(
        candidate_analysis, "_resistances", lambda *args: [current * 1.03]
    )

    candidate, rejected = evaluate_candidate(
        {"market": "KRW-TEST", "signal": "consolidation_rebreakout", "price": current},
        ticker,
        _orderbook(current),
        one,
        five,
        _config(min_score=90, availability_min_score=84),
    )

    assert rejected == []
    assert candidate is not None
    assert 84 <= candidate["score"] < 90
    assert candidate["availability_tier"] is True
    assert candidate["suggested_position_pct"] == 5
    assert any("가용성 보완형" in note for note in candidate["risk_notes"])


def test_availability_candidate_never_uses_overheated_market_to_fill_count(
    monkeypatch,
):
    one = _candles()
    five = _candles()
    current = float(one[0]["trade_price"])
    ticker = {
        "trade_price": current,
        "signed_change_rate": 0.25,
        "high_price": current * 1.08,
        "acc_trade_price_24h": 10_000_000_000,
    }
    monkeypatch.setattr(
        candidate_analysis, "_resistances", lambda *args: [current * 1.03]
    )

    candidate, rejected = evaluate_candidate(
        {"market": "KRW-TEST", "signal": "consolidation_rebreakout", "price": current},
        ticker,
        _orderbook(current),
        one,
        five,
        _config(min_score=90, availability_min_score=84),
    )

    assert candidate is None
    assert any("가용성 보완 후보 안전조건 미달" in reason for reason in rejected)


def test_elevated_risk_signal_keeps_volume_quality_strict():
    one = _candles()
    five = _candles()
    current = float(one[0]["trade_price"])
    one[1]["candle_acc_trade_volume"] = 115.0
    ticker = {
        "trade_price": current,
        "signed_change_rate": 0.12,
        "high_price": current * 1.08,
        "acc_trade_price_24h": 10_000_000_000,
    }

    candidate, rejected = evaluate_candidate(
        {"market": "KRW-TEST", "signal": "consolidation_rebreakout", "price": current},
        ticker,
        _orderbook(current),
        one,
        five,
        _config(min_score=80),
    )

    assert candidate is None
    assert any("거래량 다소 부족" in reason for reason in rejected)


def test_elevated_risk_can_balance_only_mild_candle_shape_warnings():
    one = _candles()
    five = _candles()
    current = float(one[0]["trade_price"])
    completed = float(one[1]["trade_price"])
    one[1].update(
        {
            "opening_price": completed - 0.1,
            "high_price": completed + 0.2,
            "low_price": completed - 0.2,
            "trade_price": completed,
            "candle_acc_trade_volume": 200.0,
        }
    )
    ticker = {
        "trade_price": current,
        "signed_change_rate": 0.12,
        "high_price": current * 1.08,
        "acc_trade_price_24h": 10_000_000_000,
    }

    candidate, rejected = evaluate_candidate(
        {
            "market": "KRW-TEST",
            "signal": "consolidation_rebreakout",
            "price": current,
        },
        ticker,
        _orderbook(current),
        one,
        five,
        _config(min_score=90),
    )

    assert rejected == []
    assert candidate is not None
    assert candidate["elevated_candle_balance"] is True
    assert candidate["suggested_position_pct"] == 5
    assert any("종가 위치 다소 약함" in note for note in candidate["risk_notes"])
    assert any("윗꼬리 주의" in note for note in candidate["risk_notes"])


def test_quality_below_availability_floor_is_still_rejected():
    one = _candles()
    five = _candles()
    current = float(one[0]["trade_price"])
    one[1]["candle_acc_trade_volume"] = 70.0
    ticker = {
        "trade_price": current,
        "signed_change_rate": 0.04,
        "high_price": current * 1.08,
        "acc_trade_price_24h": 10_000_000_000,
    }

    candidate, rejected = evaluate_candidate(
        {"market": "KRW-TEST", "signal": "consolidation_rebreakout", "price": current},
        ticker,
        _orderbook(current),
        one,
        five,
        _config(min_score=80),
    )

    assert candidate is None
    assert any("거래량 절대 부족" in reason for reason in rejected)


def test_btc_weakness_with_strong_orderbook_can_still_pass():
    one = _candles()
    five = _candles()
    current = float(one[0]["trade_price"])
    ticker = {
        "trade_price": current,
        "signed_change_rate": 0.08,
        "high_price": current * 1.08,
        "acc_trade_price_24h": 10_000_000_000,
    }

    candidate, rejected = evaluate_candidate(
        {"market": "KRW-TEST", "signal": "consolidation_rebreakout", "price": current},
        ticker,
        _orderbook(current),
        one,
        five,
        _config(min_score=80),
        btc_ticker={"signed_change_rate": -0.02},
    )

    assert rejected == []
    assert candidate is not None
    assert candidate["btc_weak"] is True
    assert candidate["suggested_position_pct"] == 5
    assert any("BTC 약세 감점" in note for note in candidate["risk_notes"])


def test_btc_weakness_with_weak_orderbook_is_rejected():
    one = _candles()
    five = _candles()
    current = float(one[0]["trade_price"])
    ticker = {
        "trade_price": current,
        "signed_change_rate": 0.08,
        "high_price": current * 1.08,
        "acc_trade_price_24h": 10_000_000_000,
    }

    candidate, rejected = evaluate_candidate(
        {"market": "KRW-TEST", "signal": "consolidation_rebreakout", "price": current},
        ticker,
        _orderbook(current, bid_ratio=0.79),
        one,
        five,
        _config(min_score=80, min_orderbook_ratio=0.7),
        btc_ticker={"signed_change_rate": -0.02},
    )

    assert candidate is None
    assert any("BTC 약세·호가 약세" in reason for reason in rejected)


def test_risk_off_market_breadth_is_a_hard_rejection():
    one = _candles()
    five = _candles()
    current = float(one[0]["trade_price"])
    ticker = {
        "trade_price": current,
        "signed_change_rate": 0.08,
        "high_price": current * 1.08,
        "acc_trade_price_24h": 10_000_000_000,
    }

    candidate, rejected = evaluate_candidate(
        {
            "market": "KRW-TEST",
            "signal": "consolidation_rebreakout",
            "price": current,
            "market_regime": "risk_off",
            "market_breadth_5m_pct": 27.8,
        },
        ticker,
        _orderbook(current),
        one,
        five,
        _config(min_score=80),
    )

    assert candidate is None
    assert any("시장 확산도 하드차단" in reason for reason in rejected)


def test_risk_off_breadth_rejects_nonexceptional_early_leader():
    one = _candles()
    five = _candles()
    current = float(one[0]["trade_price"])
    candidate, rejected = evaluate_candidate(
        {
            "market": "KRW-TEST",
            "signal": "consolidation_rebreakout",
            "price": current,
            "relative_strength_ready": True,
            "relative_strength_eligible": True,
            "relative_strength_percentile": 4.0,
            "best_relative_strength_percentile": 2.0,
            "momentum_5m_pct": 1.4,
            "momentum_15m_pct": 1.4,
            "early_trend": True,
            "is_reentry": True,
            "market_regime": "risk_off",
            "market_breadth_5m_pct": 34.9,
        },
        {
            "trade_price": current,
            "signed_change_rate": 0.08,
            "high_price": current * 1.06,
            "acc_trade_price_24h": 10_000_000_000,
        },
        _orderbook(current),
        one,
        five,
        _config(min_score=80),
    )

    assert candidate is None
    assert any("시장 확산도 하드차단" in reason for reason in rejected)


def test_risk_off_breadth_allows_fresh_top_five_percent_leader():
    one = _candles()
    five = _candles()
    current = float(one[0]["trade_price"])
    candidate, rejected = evaluate_candidate(
        {
            "market": "KRW-FRESH",
            "signal": "consolidation_rebreakout",
            "price": current,
            "relative_strength_ready": True,
            "relative_strength_eligible": True,
            "relative_strength_percentile": 4.0,
            "best_relative_strength_percentile": 2.0,
            "momentum_5m_pct": 1.6,
            "momentum_15m_pct": 1.7,
            "early_trend": True,
            "market_regime": "risk_off",
            "market_breadth_5m_pct": 30.0,
        },
        {
            "trade_price": current,
            "signed_change_rate": 0.06,
            "high_price": current * 1.08,
            "acc_trade_price_24h": 10_000_000_000,
        },
        _orderbook(current),
        one,
        five,
        _config(min_score=80),
    )

    assert rejected == []
    assert candidate is not None
    assert candidate["selection_lane"] == "early_leader"
    assert any("시장 약세 중 상대강도 선도 유지" in note for note in candidate["risk_notes"])


def test_reentry_rejects_stale_leader_far_below_day_high():
    one = _candles()
    five = _candles()
    current = float(one[0]["trade_price"])
    candidate, rejected = evaluate_candidate(
        {
            "market": "KRW-ARX",
            "signal": "consolidation_rebreakout",
            "price": current,
            "is_reentry": True,
        },
        {
            "trade_price": current,
            "signed_change_rate": 0.18,
            "high_price": current / 0.89,
            "acc_trade_price_24h": 20_000_000_000,
        },
        _orderbook(current),
        one,
        five,
        _config(min_score=80),
    )

    assert candidate is None
    assert any("당일 고점 대비 낙폭 과다" in reason for reason in rejected)


def test_only_exceptional_early_leader_softens_breadth_and_moderate_orderbook():
    one = _candles()
    five = _candles()
    current = float(one[0]["trade_price"])
    ticker = {
        "trade_price": current,
        "signed_change_rate": 0.06,
        "high_price": current * 1.08,
        "acc_trade_price_24h": 10_000_000_000,
    }
    alert = {
        "market": "KRW-TEST",
        "signal": "consolidation_rebreakout",
        "price": current,
        "relative_strength_ready": True,
        "relative_strength_eligible": True,
        "relative_strength_rank": 2,
        "relative_strength_universe": 100,
        "relative_strength_percentile": 0.8,
        "momentum_5m_pct": 2.2,
        "momentum_15m_pct": 2.4,
        "early_trend": True,
        "market_regime": "risk_off",
        "market_breadth_5m_pct": 25.0,
    }

    candidate, rejected = evaluate_candidate(
        alert,
        ticker,
        _orderbook(current, bid_ratio=0.6),
        one,
        five,
        _config(min_score=80, availability_balance_enabled=False),
    )

    assert rejected == []
    assert candidate is not None
    assert candidate["selection_lane"] == "early_leader"
    assert candidate["early_leader_lane"] is True
    assert any("시장 약세 중 상대강도 선도" in note for note in candidate["risk_notes"])
    assert any("호가 지지 약함" in note for note in candidate["risk_notes"])


def test_early_leader_lane_can_treat_nearby_resistance_as_breakout_level(monkeypatch):
    one = _candles()
    five = _candles()
    current = float(one[0]["trade_price"])
    monkeypatch.setattr(
        candidate_analysis, "_resistances", lambda *args: [current * 1.015]
    )
    ticker = {
        "trade_price": current,
        "signed_change_rate": 0.06,
        "high_price": current * 1.08,
        "acc_trade_price_24h": 10_000_000_000,
    }

    candidate, rejected = evaluate_candidate(
        {
            "market": "KRW-TEST",
            "signal": "consolidation_rebreakout",
            "price": current,
            "relative_strength_ready": True,
            "relative_strength_eligible": True,
            "relative_strength_rank": 1,
            "relative_strength_universe": 100,
            "relative_strength_percentile": 1.0,
            "momentum_5m_pct": 2.0,
            "momentum_15m_pct": 2.5,
            "early_trend": True,
        },
        ticker,
        _orderbook(current),
        one,
        five,
        _config(min_score=80, availability_balance_enabled=False),
    )

    assert rejected == []
    assert candidate is not None
    assert candidate["leader_resistance_override"] is True
    assert candidate["target_1"] > candidate["resistance_price"]
    assert any("근접 저항" in note for note in candidate["risk_notes"])


def test_persisted_leader_rebreakout_merges_breakout_resistance_cluster(monkeypatch):
    one = _candles()
    five = _candles()
    current = float(one[0]["trade_price"])
    monkeypatch.setattr(
        candidate_analysis, "_resistances", lambda *args: [current * 1.006]
    )
    ticker = {
        "trade_price": current,
        "signed_change_rate": 0.06,
        "high_price": current * 1.006,
        "acc_trade_price_24h": 10_000_000_000,
    }

    candidate, rejected = evaluate_candidate(
        {
            "market": "KRW-TEST",
            "signal": "consolidation_rebreakout",
            "price": current,
            "relative_strength_ready": True,
            "relative_strength_eligible": True,
            "relative_strength_rank": 13,
            "relative_strength_universe": 210,
            "relative_strength_percentile": 6.19,
            "best_relative_strength_percentile": 4.59,
            "momentum_5m_pct": 2.07,
            "momentum_15m_pct": 1.47,
            "early_trend": True,
        },
        ticker,
        _orderbook(current, bid_ratio=0.69),
        one,
        five,
        _config(min_score=80, availability_balance_enabled=False),
    )

    assert rejected == []
    assert candidate is not None
    assert candidate["early_leader_lane"] is True
    assert candidate["leadership_persisted"] is True
    assert candidate["best_relative_strength_percentile"] == 4.59
    assert candidate["breakout_cluster_ignored"] is True
    assert candidate["resistance_confirmed"] is False
    assert any("동일 돌파 구간" in note for note in candidate["risk_notes"])


def test_fast_leader_can_pass_before_completed_minute_without_chasing(monkeypatch):
    one = _candles()
    five = _candles()
    current = float(one[0]["trade_price"])
    opened = datetime(2026, 9, 16, tzinfo=timezone.utc)
    for index, candle in enumerate(one):
        candle["candle_date_time_utc"] = (opened - timedelta(minutes=index)).isoformat()
    ticker = {
        "trade_price": current,
        "signed_change_rate": 0.06,
        "high_price": current * 1.003,
        "acc_trade_price_24h": 10_000_000_000,
    }
    monkeypatch.setattr(
        candidate_analysis, "_resistances", lambda *args: [current * 1.003]
    )
    alert = {
        "time_utc": "2026-09-16T00:00:50+00:00",
        "confirmation_started_at_utc": "2026-09-16T00:00:50+00:00",
        "market": "KRW-FOLD",
        "signal": "leader_volume_acceleration",
        "price": current,
        "breakout_level": current,
        "fast_leader": True,
        "relative_strength_ready": True,
        "relative_strength_eligible": True,
        "relative_strength_rank": 3,
        "relative_strength_universe": 200,
        "relative_strength_percentile": 1.5,
        "momentum_5m_pct": 6.55,
        "momentum_15m_pct": 5.49,
        "early_trend": True,
        "market_regime": "neutral",
        "preleader_volume_ratio_10m": 97.19,
        "preleader_volume_ratio_30m": 144.65,
    }

    candidate, rejected = evaluate_candidate(
        alert,
        ticker,
        _orderbook(current, bid_ratio=0.8),
        one,
        five,
        _config(min_score=80, availability_balance_enabled=False),
        as_of=opened + timedelta(seconds=55),
    )

    assert rejected == []
    assert candidate is not None
    assert candidate["selection_lane"] == "fast_leader"
    assert candidate["fast_leader"] is True
    assert candidate["valid_seconds"] == 120
    assert candidate["suggested_position_pct"] == 5
    assert candidate["breakout_cluster_ignored"] is True
    assert candidate["entry_high"] <= current * 1.015
    assert not any("완료 1분봉 돌파 확정" in item for item in candidate["reasons"])


def test_fast_leader_rejects_price_beyond_initial_chase_cap():
    one = _candles()
    five = _candles()
    signal_price = float(one[0]["trade_price"])
    current = signal_price * 1.02
    ticker = {
        "trade_price": current,
        "signed_change_rate": 0.08,
        "high_price": current * 1.05,
        "acc_trade_price_24h": 10_000_000_000,
    }
    alert = {
        "market": "KRW-FOLD",
        "signal": "leader_volume_acceleration",
        "price": signal_price,
        "breakout_level": signal_price,
        "fast_leader": True,
        "relative_strength_ready": True,
        "relative_strength_eligible": True,
        "relative_strength_rank": 1,
        "relative_strength_universe": 200,
        "relative_strength_percentile": 0.5,
        "momentum_5m_pct": 6.0,
        "momentum_15m_pct": 5.0,
        "early_trend": True,
        "market_regime": "neutral",
        "preleader_volume_ratio_10m": 20.0,
        "preleader_volume_ratio_30m": 30.0,
    }

    candidate, rejected = evaluate_candidate(
        alert,
        ticker,
        _orderbook(current),
        one,
        five,
        _config(min_score=80, availability_balance_enabled=False),
    )

    assert candidate is None
    assert any("신호가 대비 추격 구간" in reason for reason in rejected)


def test_survival_recheck_allows_cooling_volume_but_rejects_structure_loss():
    candles = _candles()
    current = float(candles[0]["trade_price"])
    candles[1]["candle_acc_trade_volume"] = 48.0
    candles[2]["candle_acc_trade_volume"] = 100.0
    for candle in candles[3:23]:
        candle["candle_acc_trade_volume"] = 5.0
    candidate = {
        "breakout_level": float(candles[1]["trade_price"]) - 0.1,
        "stop_price": current * 0.97,
        "chase_limit": current * 1.02,
    }

    metrics, rejected = validate_candidate_survival(
        candidate,
        {"trade_price": current},
        [_orderbook(current, bid_ratio=0.6)] * 3,
        candles,
        _config(),
    )

    assert rejected == []
    assert metrics["survival_volume_vs_previous"] == 0.48

    candles[1]["trade_price"] = candidate["breakout_level"] * 0.99
    _, rejected = validate_candidate_survival(
        candidate,
        {"trade_price": current},
        [_orderbook(current, bid_ratio=0.6)] * 3,
        candles,
        _config(),
    )

    assert any("돌파선 유지 실패" in reason for reason in rejected)


def test_high_day_change_is_a_score_penalty_not_an_automatic_rejection():
    one = _candles()
    five = _candles()
    current = float(one[0]["trade_price"])
    ticker = {
        "trade_price": current,
        "signed_change_rate": 0.25,
        "high_price": current * 1.08,
        "acc_trade_price_24h": 10_000_000_000,
    }

    candidate, rejected = evaluate_candidate(
        {"market": "KRW-TEST", "signal": "consolidation_rebreakout", "price": current},
        ticker,
        _orderbook(current),
        one,
        five,
        _config(min_score=80),
    )

    assert rejected == []
    assert candidate is not None
    assert candidate["day_overheated"] is True
    assert candidate["condition_score"] == candidate["score"]
    assert candidate["suggested_position_pct"] == 5
    assert any("당일 과열 감점" in note for note in candidate["risk_notes"])


def _explosive_setup():
    now = datetime(2026, 10, 1, 7, 30, 5, tzinfo=timezone.utc)
    def bars(minutes, latest_price=100.1):
        boundary = now.replace(second=0) - timedelta(minutes=int(now.timestamp() // 60) % minutes)
        result = []
        for index in range(120):
            result.append({"opening_price": 100.0, "high_price": 100.2, "low_price": 99.95,
                           "trade_price": 100.1, "candle_acc_trade_volume": 100,
                           "candle_acc_trade_price": 10010,
                           "candle_date_time_utc": (boundary - timedelta(minutes=index * minutes)).isoformat()})
        return result
    one, five, fifteen, btc = bars(1), bars(5), bars(15), bars(5)
    # Latest 1m has a long wick, weak volume and RSI heat; completed 5m is clean.
    one[1].update(opening_price=100.9, high_price=102, low_price=100.9,
                  trade_price=101, candle_acc_trade_volume=10)
    five[1].update(opening_price=100.3, high_price=101.05, low_price=100.3,
                   trade_price=101, candle_acc_trade_volume=1000)
    fifteen[1]["candle_acc_trade_volume"] = 500
    alert = {"market": "KRW-TEST", "signal": "breakout", "price": 101,
             "breakout_level": 100.2, "relative_strength_ready": True,
             "relative_strength_eligible": True, "relative_strength_percentile": 0.5,
             "relative_strength_rank": 1, "relative_strength_universe": 200,
             "momentum_5m_pct": 2, "momentum_15m_pct": 3,
             "early_trend": False, "market_regime": "risk_off", "market_breadth_5m_pct": 12}
    ticker = {"trade_price": 101, "signed_change_rate": 0.10,
              "high_price": 101.05, "acc_trade_price_24h": 10_000_000_000}
    return now, one, five, fifteen, btc, alert, ticker


def _evaluate_explosive(setup, **config):
    now, one, five, fifteen, btc, alert, ticker = setup
    return evaluate_candidate(alert, ticker, _orderbook(ticker["trade_price"], 0.7), one, five,
                              _config(**{"double_bb_enabled": True, "min_score": 80, **config}),
                              candles_15m=fifteen, btc_candles_5m=btc,
                              btc_candles_15m=btc, as_of=now)


def test_completed_explosive_leader_can_use_five_minute_evidence():
    setup = _explosive_setup()
    candidate, reasons = _evaluate_explosive(setup)
    assert candidate is not None, reasons
    assert candidate["selection_lane"] == "explosive_leader"
    assert candidate["volume_timeframe"] == "5분"
    assert candidate["risk_reward"] >= 2
    assert candidate["explosive_context"]["volume_5m"] == 10
    assert any("RSI" in note for note in candidate["risk_notes"])
    now, one, five, fifteen, _, _, ticker = setup
    _, reasons = validate_candidate_survival(candidate, ticker, [_orderbook(101, 0.7)],
                                             one, _config(), as_of=now,
                                             candles_5m=five, candles_15m=fifteen)
    assert reasons == []
    _, reasons = validate_candidate_survival(candidate, ticker, [_orderbook(101, 0.7)],
                                             one, _config(), as_of=now)
    assert any("5·15분" in reason for reason in reasons)


@pytest.mark.parametrize("cause", ["incomplete", "weak_five", "weak_fifteen", "gap", "wick", "rank", "btc", "wide_stop", "near_resistance", "chase"])
def test_explosive_lane_keeps_completed_structure_and_risk_guards(cause):
    setup = _explosive_setup()
    now, one, five, fifteen, btc, alert, ticker = setup
    if cause == "incomplete":
        five[0].update(five[1]); five[0]["candle_date_time_utc"] = now.replace(second=0).isoformat()
        five[1]["candle_acc_trade_volume"] = 100
    elif cause == "weak_five": five[1]["candle_acc_trade_volume"] = 100
    elif cause == "weak_fifteen": fifteen[1]["candle_acc_trade_volume"] = 20
    elif cause == "gap": one.pop(3)
    elif cause == "wick": five[1]["high_price"] = 103
    elif cause == "rank": alert["relative_strength_percentile"] = 9
    elif cause == "btc": btc[1]["trade_price"] = 98
    elif cause == "wide_stop": alert["breakout_level"] = 95
    elif cause == "near_resistance":
        # Repeated prior peaks remain hard overhead supply, not today's unconfirmed high.
        for bars in (five, fifteen):
            for index in (5, 10, 15): bars[index]["high_price"] = 101.5
    elif cause == "chase": ticker["trade_price"] = 103
    candidate, reasons = _evaluate_explosive(setup)
    assert candidate is None, (cause, candidate)
    assert reasons


def test_explosive_path_can_be_disabled_without_relaxing_ordinary_screen():
    candidate, reasons = _evaluate_explosive(_explosive_setup(), explosive_leader_enabled=False)
    assert candidate is None
    assert any("1분봉 거래량" in reason for reason in reasons)


def test_explosive_lane_preserves_deductions_with_separate_explicit_score_floor():
    candidate, reasons = _evaluate_explosive(_explosive_setup(), min_score=95, availability_min_score=86)
    assert candidate is not None, reasons
    assert 80 <= candidate["score"] < 95
    assert not candidate["availability_tier"]
    assert candidate["completed_1m_volume_ratio"] < 1
    candidate, reasons = _evaluate_explosive(_explosive_setup(), explosive_leader_min_score=95)
    assert candidate is None and any("점수 부족" in reason for reason in reasons)


def test_explosive_first_retest_requires_actual_completed_contact():
    from candidate_analysis import _explosive_bar_context
    now, one, five, fifteen, _, _, _ = _explosive_setup()
    # Move the impulse back one completed 5m bar; its original ceiling is 100.2.
    five[2].update({k: v for k, v in five[1].items() if k != "candle_date_time_utc"})
    five[1].update(opening_price=100.2, high_price=100.9, low_price=100.15,
                   trade_price=100.85, candle_acc_trade_volume=400)
    context = _explosive_bar_context(one, five, fifteen, now)
    assert context["confirmed"] and context["retest"]
    five[1]["low_price"] = 101.1
    five[1]["high_price"] = 102
    five[1]["trade_price"] = 101.9
    assert not _explosive_bar_context(one, five, fifteen, now)["confirmed"]


def _ordinary_hot_breakout(monkeypatch):
    import candidate_analysis
    setup = _explosive_setup()
    now, one, five, fifteen, btc, alert, ticker = setup
    five[1]["candle_acc_trade_volume"] = 200  # 2x, below the explosive 3x floor
    one[1].update(opening_price=100.8, high_price=101.02, low_price=100.8,
                  trade_price=101, candle_acc_trade_volume=1000)
    alert.update(early_trend=True, market_regime="risk_on", market_breadth_5m_pct=60)
    monkeypatch.setattr(candidate_analysis, "_double_bollinger_context",
                        lambda *a, **k: {"ready": True, "confirmed": True,
                                         "fake_breakout": False, "status": "confirmed"})
    return setup


def _evaluate_hot(setup, book=2):
    now, one, five, fifteen, btc, alert, ticker = setup
    return evaluate_candidate(alert, ticker, _orderbook(ticker["trade_price"], book),
                              one, five, _config(double_bb_enabled=True, min_score=80),
                              candles_15m=fifteen, btc_candles_5m=btc,
                              btc_candles_15m=btc, as_of=now)


def test_rsi_only_exception_retains_ordinary_lane_and_survival(monkeypatch):
    setup = _ordinary_hot_breakout(monkeypatch)
    candidate, reasons = _evaluate_hot(setup)
    assert candidate is not None, reasons
    assert candidate["rsi_breakout_exception"]
    assert candidate["selection_lane"] != "explosive_leader"
    assert any("RSI" in note for note in candidate["risk_notes"])
    now, one, five, fifteen, _, _, ticker = setup
    _, rejected = validate_candidate_survival(candidate, ticker, [_orderbook(101, 2)],
                                              one, _config(), as_of=now,
                                              candles_5m=five, candles_15m=fifteen)
    assert rejected == []
    _, rejected = validate_candidate_survival(candidate, ticker, [_orderbook(101, .7)],
                                              one, _config(), as_of=now,
                                              candles_5m=five, candles_15m=fifteen)
    assert any("RSI 예외" in reason for reason in rejected)


@pytest.mark.parametrize("cause", ["volume5", "volume15", "gap", "rank", "book", "btc", "wick"])
def test_rsi_exception_cannot_waive_missing_evidence(monkeypatch, cause):
    setup = _ordinary_hot_breakout(monkeypatch)
    _, one, five, fifteen, btc, alert, _ = setup
    if cause == "volume5": five[1]["candle_acc_trade_volume"] = 100
    elif cause == "volume15": fifteen[1]["candle_acc_trade_volume"] = 50
    elif cause == "gap": one.pop(3)
    elif cause == "rank": alert["relative_strength_percentile"] = 5
    elif cause == "btc": btc[1]["trade_price"] = 98
    elif cause == "wick": five[1]["high_price"] = 103
    candidate, reasons = _evaluate_hot(setup, book=.7 if cause == "book" else 2)
    assert candidate is None
    assert any("RSI" in reason for reason in reasons)


@pytest.mark.parametrize("cause", ["held", "lost_support", "unbroken", "incomplete"])
def test_resistance_reclassification_needs_completed_clearance_and_live_hold(cause):
    setup = _explosive_setup()
    now, one, five, fifteen, btc, alert, ticker = setup
    for index in (20, 25, 30): five[index]["high_price"] = 101.15
    five[1].update(high_price=101.42, trade_price=101.4)
    ticker["high_price"] = 101.42
    if cause == "lost_support": ticker["trade_price"] = 100.9
    elif cause == "unbroken":
        for index in (20, 25, 30): five[index]["high_price"] = 101.5
    elif cause == "incomplete":
        five[0].update(high_price=101.42, trade_price=101.4)
        five[1].update(high_price=101.05, trade_price=101)
    candidate, reasons = _evaluate_explosive(setup)
    if cause == "held":
        assert candidate is not None, reasons
        assert 101.15 in candidate["cleared_resistance_levels"]
    else:
        assert candidate is None
        assert any("저항" in reason for reason in reasons)
