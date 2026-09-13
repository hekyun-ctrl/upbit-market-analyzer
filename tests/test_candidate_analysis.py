from candidate_analysis import CandidateConfig, evaluate_candidate


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


def test_weak_orderbook_is_rejected():
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
        alert, ticker, _orderbook(current, bid_ratio=0.2), one, five, _config()
    )

    assert candidate is None
    assert any("호가 지지 극단적 부족" in reason for reason in rejected)


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


def test_resistance_room_below_hard_floor_is_rejected():
    one = _candles()
    five = _candles()
    current = float(one[0]["trade_price"])
    ticker = {
        "trade_price": current,
        "signed_change_rate": 0.08,
        "high_price": current * 1.02,
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
    assert any("저항까지 여유 부족" in reason for reason in rejected)


def test_orderbook_support_must_persist_across_samples():
    one = _candles()
    five = _candles()
    current = float(one[0]["trade_price"])
    ticker = {
        "trade_price": current,
        "signed_change_rate": 0.08,
        "high_price": current * 1.08,
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
        _config(),
        orderbook_samples=samples,
    )

    assert candidate is None
    assert any("호가 지지 극단적 부족" in reason for reason in rejected)


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
