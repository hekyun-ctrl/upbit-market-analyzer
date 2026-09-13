import asyncio

from candidate_analysis import CandidateConfig
from monitor import (
    MONITOR_STATE,
    AlertDispatcher,
    CandidateAnalyzer,
    MonitorConfig,
    SignalEngine,
    _candidate_text,
)


def _config(**overrides):
    values = {
        "markets": ("KRW-IQ",),
        "all_krw_markets": False,
        "price_surge_1m_pct": 1.5,
        "breakout_pct": 0.4,
        "volume_ratio": 2.0,
        "min_trade_value_krw": 1_000.0,
        "alert_cooldown_seconds": 600,
        "max_alerts_per_minute": 5,
        "rebreakout_enabled": True,
        "rebreakout_consolidation_seconds": 1800,
        "rebreakout_max_range_pct": 3.0,
        "rebreakout_price_buffer_pct": 0.3,
        "rebreakout_min_volume_ratio": 3.0,
    }
    values.update(overrides)
    return MonitorConfig(**values)


def test_price_and_volume_surge_is_detected_after_warmup():
    engine = SignalEngine(_config())
    alerts = []
    base_ms = 1_800_000_000_000

    for second in range(121):
        price = 100.0 if second < 61 else 100.0 + (second - 60) * 0.03
        volume = 0.1 if second < 61 else 1.0
        alerts.extend(engine.update("KRW-IQ", price, volume, base_ms + second * 1000))

    assert any(alert["signal"] == "price_volume_surge" for alert in alerts)


def test_alert_cooldown_blocks_duplicate_signal():
    engine = SignalEngine(_config(alert_cooldown_seconds=600))
    alerts = []
    base_ms = 1_800_000_000_000

    for second in range(150):
        price = 100.0 if second < 61 else 103.0
        volume = 0.1 if second < 61 else 1.0
        alerts.extend(engine.update("KRW-IQ", price, volume, base_ms + second * 1000))

    surge_alerts = [a for a in alerts if a["signal"] == "price_volume_surge"]
    assert len(surge_alerts) == 1


def test_invalid_non_positive_trade_is_ignored():
    engine = SignalEngine(_config())
    assert engine.update("KRW-IQ", 0, 10, 1_800_000_000_000) == []


def test_long_consolidation_volume_rebreakout_is_detected():
    engine = SignalEngine(_config(min_trade_value_krw=1_000.0))
    alerts = []
    base_ms = 1_800_000_000_000

    for second in range(1921):
        if second < 1861:
            price = 100.0 + (0.2 if second % 20 < 10 else -0.2)
            volume = 0.2
        else:
            price = 100.2 + (second - 1860) * 0.02
            volume = 2.0
        alerts.extend(engine.update("KRW-IQ", price, volume, base_ms + second * 1000))

    rebreakouts = [
        alert for alert in alerts if alert["signal"] == "consolidation_rebreakout"
    ]
    assert len(rebreakouts) == 1
    assert rebreakouts[0]["consolidation_minutes"] == 30
    assert rebreakouts[0]["consolidation_range_pct"] <= 3.0
    assert rebreakouts[0]["volume_ratio_vs_consolidation"] >= 3.0
    assert rebreakouts[0]["breakout_level"] > rebreakouts[0]["consolidation_high"]


def test_wide_range_is_not_classified_as_consolidation_rebreakout():
    engine = SignalEngine(_config(min_trade_value_krw=1_000.0))
    alerts = []
    base_ms = 1_800_000_000_000

    for second in range(1921):
        if second < 1861:
            price = 100.0 + (4.0 if second % 20 < 10 else -4.0)
            volume = 0.2
        else:
            price = 105.0 + (second - 1860) * 0.02
            volume = 2.0
        alerts.extend(engine.update("KRW-IQ", price, volume, base_ms + second * 1000))

    assert not any(alert["signal"] == "consolidation_rebreakout" for alert in alerts)


def test_rebreakout_requires_volume_expansion():
    engine = SignalEngine(_config(min_trade_value_krw=100.0, price_surge_1m_pct=10.0))
    alerts = []
    base_ms = 1_800_000_000_000

    for second in range(1921):
        if second < 1861:
            price = 100.0 + (0.2 if second % 20 < 10 else -0.2)
        else:
            price = 100.2 + (second - 1860) * 0.02
        alerts.extend(engine.update("KRW-IQ", price, 0.2, base_ms + second * 1000))

    assert not any(alert["signal"] == "consolidation_rebreakout" for alert in alerts)


def test_observation_telegram_delivery_can_be_disabled(monkeypatch):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "test-token")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "test-chat")
    monkeypatch.setenv("TELEGRAM_SEND_OBSERVATION_ALERTS", "false")
    monkeypatch.setenv("TELEGRAM_SEND_CANDIDATE_ALERTS", "true")

    dispatcher = AlertDispatcher()
    assert dispatcher.mode == "telegram"
    assert dispatcher.observation_delivery_enabled is False
    assert dispatcher.candidate_delivery_enabled is True

    class UnexpectedClient:
        def __init__(self, *args, **kwargs):
            raise AssertionError("suppressed observation must not call Telegram")

    monkeypatch.setattr("monitor.httpx.AsyncClient", UnexpectedClient)
    asyncio.run(dispatcher.send({"market": "KRW-IQ", "signal": "breakout"}))


def test_telegram_delivery_filters_default_to_enabled(monkeypatch):
    monkeypatch.delenv("TELEGRAM_SEND_OBSERVATION_ALERTS", raising=False)
    monkeypatch.delenv("TELEGRAM_SEND_CANDIDATE_ALERTS", raising=False)
    monkeypatch.delenv("TELEGRAM_CANDIDATE_MIN_TARGET_2_PCT", raising=False)
    monkeypatch.delenv("TELEGRAM_CANDIDATE_MIN_SCORE", raising=False)
    dispatcher = AlertDispatcher()
    assert dispatcher.observation_delivery_enabled is True
    assert dispatcher.candidate_delivery_enabled is True
    assert dispatcher.candidate_min_target_2_pct == 5.0
    assert dispatcher.candidate_min_score == 0


def test_candidate_without_minimum_second_target_is_not_sent(monkeypatch):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "test-token")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "test-chat")
    monkeypatch.setenv("TELEGRAM_SEND_CANDIDATE_ALERTS", "true")
    monkeypatch.setenv("TELEGRAM_CANDIDATE_MIN_TARGET_2_PCT", "5")

    dispatcher = AlertDispatcher()
    assert dispatcher.candidate_min_target_2_pct == 5.0

    class UnexpectedClient:
        def __init__(self, *args, **kwargs):
            raise AssertionError(
                "candidate without a 5% second target must not call Telegram"
            )

    monkeypatch.setattr("monitor.httpx.AsyncClient", UnexpectedClient)
    asyncio.run(
        dispatcher.send_candidate(
            {
                "market": "KRW-IQ",
                "signal": "entry_candidate",
                "score": 100,
                "target_1_pct": 3.0,
                "target_2_pct": 4.9,
            }
        )
    )


def test_candidate_below_minimum_score_is_not_sent(monkeypatch):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "test-token")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "test-chat")
    monkeypatch.setenv("TELEGRAM_SEND_CANDIDATE_ALERTS", "true")
    monkeypatch.setenv("TELEGRAM_CANDIDATE_MIN_TARGET_2_PCT", "5")
    monkeypatch.setenv("TELEGRAM_CANDIDATE_MIN_SCORE", "90")

    dispatcher = AlertDispatcher()
    assert dispatcher.candidate_min_score == 90

    class UnexpectedClient:
        def __init__(self, *args, **kwargs):
            raise AssertionError("candidate below score 90 must not call Telegram")

    monkeypatch.setattr("monitor.httpx.AsyncClient", UnexpectedClient)
    asyncio.run(
        dispatcher.send_candidate(
            {
                "market": "KRW-IQ",
                "signal": "entry_candidate",
                "score": 89,
                "target_1_pct": 3.0,
                "target_2_pct": 5.0,
            }
        )
    )


def test_price_retake_schedules_one_reentry_check(monkeypatch):
    monkeypatch.setenv("ENABLE_CANDIDATE_ANALYSIS", "true")
    analyzer = CandidateAnalyzer(CandidateConfig.from_env(), AlertDispatcher())
    analyzer._lifecycles["KRW-IQ"] = {
        "source_signal": "breakout",
        "entry_low": 100.0,
        "entry_high": 101.0,
        "chase_limit": 102.0,
        "stop_price": 98.0,
        "target_1": 105.0,
        "entry_price": 100.5,
        "max_price": 100.5,
        "min_price": 100.5,
        "score": 95,
        "is_reentry": False,
        "created_at": 1_800_000_000.0,
        "breakout_level": 99.8,
        "waiting_retest": False,
        "expires_at": 9_999_999_999.0,
    }

    seen = []

    async def fake_analyze(alert):
        seen.append(alert)

    monkeypatch.setattr(analyzer, "_analyze", fake_analyze)

    async def run():
        assert analyzer.observe_price("KRW-IQ", 102.1) is False
        assert analyzer.observe_price("KRW-IQ", 100.5) is True
        await asyncio.sleep(0)

    asyncio.run(run())
    assert len(seen) == 1
    assert seen[0]["is_reentry"] is True


def test_candidate_outcome_records_target_before_stop(monkeypatch):
    monkeypatch.setenv("ENABLE_CANDIDATE_ANALYSIS", "true")
    analyzer = CandidateAnalyzer(CandidateConfig.from_env(), AlertDispatcher())
    MONITOR_STATE.candidate_outcomes.clear()
    analyzer._lifecycles["KRW-IQ"] = {
        "source_signal": "breakout",
        "entry_low": 100.0,
        "entry_high": 101.0,
        "chase_limit": 102.0,
        "stop_price": 98.0,
        "target_1": 105.0,
        "entry_price": 100.5,
        "max_price": 100.5,
        "min_price": 100.5,
        "score": 95,
        "is_reentry": False,
        "created_at": 1_800_000_000.0,
        "breakout_level": 99.8,
        "waiting_retest": False,
        "expires_at": 9_999_999_999.0,
    }

    assert analyzer.observe_price("KRW-IQ", 105.0) is False
    performance = MONITOR_STATE.candidate_performance()
    assert performance["target_1_first"] == 1
    assert performance["stop_first"] == 0
    assert performance["target_1_first_rate_pct"] == 100.0
    assert "KRW-IQ" not in analyzer._lifecycles


def test_rejected_candidate_is_rechecked_on_consolidation_breakout(monkeypatch):
    monkeypatch.setenv("ENABLE_CANDIDATE_ANALYSIS", "true")
    analyzer = CandidateAnalyzer(CandidateConfig.from_env(), AlertDispatcher())
    analyzer._watchlist["KRW-IQ"] = {
        "market": "KRW-IQ",
        "created_at": 1_800_000_000.0,
        "first_signal_time_utc": "2026-09-12T10:00:00+00:00",
        "first_signal_price": 100.0,
        "recheck_count": 0,
        "expires_at": 9_999_999_999.0,
    }
    analyzer._last_checked_at["KRW-IQ"] = 9_999_999_998.0
    seen = []

    async def fake_analyze(alert):
        seen.append(alert)

    monkeypatch.setattr(analyzer, "_analyze", fake_analyze)

    async def run():
        assert analyzer.schedule(
            {
                "time_utc": "2026-09-12T11:00:00+00:00",
                "market": "KRW-IQ",
                "signal": "consolidation_rebreakout",
                "price": 101.0,
                "breakout_level": 100.8,
            }
        )
        await asyncio.sleep(0)

    asyncio.run(run())
    assert len(seen) == 1
    assert seen[0]["is_reentry"] is True
    assert seen[0]["watchlist_recheck"] is True


def test_every_screened_signal_tracks_five_percent_before_three_percent(monkeypatch):
    monkeypatch.setenv("ENABLE_CANDIDATE_ANALYSIS", "true")
    analyzer = CandidateAnalyzer(CandidateConfig.from_env(), AlertDispatcher())
    MONITOR_STATE.signal_outcomes.clear()
    analyzer._start_signal_track(
        {
            "time_utc": "2026-09-12T10:00:00+00:00",
            "market": "KRW-IQ",
            "signal": "breakout",
            "price": 100.0,
        },
        1_800_000_000.0,
    )

    analyzer._observe_signal_track("KRW-IQ", 105.0, 1_800_000_120.0)

    performance = MONITOR_STATE.candidate_performance()["raw_signal_performance"]
    assert performance["sample_count"] == 1
    assert performance["target_first"] == 1
    assert performance["stop_first"] == 0
    assert performance["target_first_rate_pct"] == 100.0


def test_candidate_message_labels_score_as_condition_score():
    text = _candidate_text(
        {
            "market": "KRW-IQ",
            "score": 92,
            "current_price": 100.0,
            "entry_low": 99.0,
            "entry_high": 100.0,
            "chase_limit": 101.0,
            "stop_price": 97.0,
            "target_1": 105.0,
            "target_2": 108.0,
            "target_1_pct": 5.5,
            "target_2_pct": 8.5,
            "target_mode": "구조적 저항 기반",
            "resistance_room_pct": 5.5,
            "risk_reward": 2.1,
            "suggested_position_pct": 5,
            "valid_seconds": 300,
            "risk_notes": ["BTC 약세 감점 -10점"],
            "reasons": ["완료 1분봉 돌파 확정"],
        }
    )

    assert "조건점수 92/100" in text
    assert "위험 감점: BTC 약세 감점 -10점" in text
