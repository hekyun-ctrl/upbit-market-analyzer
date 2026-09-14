from datetime import datetime, timedelta, timezone

import candidate_analysis
from candidate_analysis import (
    CandidateConfig,
    _completed,
    _completed_after_signal,
    _resistances,
    evaluate_candidate,
)


def _config(**overrides):
    values = {
        "enabled": True,
        "confirm_seconds": 30,
        "min_score": 80,
        "cooldown_seconds": 900,
        "valid_seconds": 300,
        "max_day_change_pct": 20.0,
        "max_rsi_1m": 78.0,
        "max_rsi_5m": 75.0,
        "hard_max_rsi_1m": 88.0,
        "hard_max_rsi_5m": 85.0,
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
        "availability_balance_enabled": True,
        "availability_max_soft_warnings": 2,
        "availability_soft_penalty": 4,
        "availability_min_volume_ratio": 0.85,
        "availability_min_volume_vs_previous": 0.45,
        "availability_min_close_position": 0.35,
        "availability_max_upper_wick_ratio": 0.65,
        "availability_resistance_floor_pct": 1.5,
        "availability_min_score": 84,
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
    assert candidate["target_mode"] in {"균형 위험비형", "강한 추세 확장형"}
    assert candidate["resistance_room_pct"] >= 5.0
    assert candidate["risk_reward"] >= 2.0


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


def test_extremely_weak_orderbook_is_only_a_score_penalty():
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

    assert rejected == []
    assert candidate is not None
    assert candidate["suggested_position_pct"] == 5
    assert any("호가 지지 매우 약함" in note for note in candidate["risk_notes"])


def test_moderately_weak_orderbook_is_a_score_penalty():
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

    assert rejected == []
    assert candidate is not None
    assert candidate["suggested_position_pct"] == 5
    assert any("호가 지지 약함" in note for note in candidate["risk_notes"])


def test_moderate_spread_is_a_score_penalty_but_extreme_spread_is_rejected():
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
    assert rejected == []
    assert candidate is not None
    assert any("호가 스프레드 주의" in note for note in candidate["risk_notes"])

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


def test_orderbook_support_must_persist_to_avoid_penalty():
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

    assert rejected == []
    assert candidate is not None
    assert any("호가 지지 매우 약함" in note for note in candidate["risk_notes"])


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


def test_btc_weakness_is_a_score_penalty_not_an_automatic_rejection():
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
