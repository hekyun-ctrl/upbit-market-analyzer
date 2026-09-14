import asyncio

from candidate_analysis import CandidateConfig
from monitor import (
    MONITOR_STATE,
    AlertDispatcher,
    CandidateAnalyzer,
    MonitorConfig,
    SignalEngine,
    _candidate_text,
    _revalidate_candidate_for_dispatch,
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
        "relative_strength_enabled": True,
        "relative_strength_min_universe": 20,
        "relative_strength_top_percent": 15.0,
        "relative_strength_min_5m_pct": 0.8,
        "relative_strength_stale_seconds": 120,
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


def test_relative_strength_ranks_the_leading_market():
    engine = SignalEngine(
        _config(
            price_surge_1m_pct=100.0,
            breakout_pct=100.0,
            rebreakout_enabled=False,
            relative_strength_min_universe=20,
        )
    )
    base_ms = 1_800_000_000_000
    for market_index in range(20):
        market = f"KRW-T{market_index:02d}"
        gain = 0.02 if market_index == 0 else market_index * 0.0001
        for second in range(306):
            engine.update(
                market,
                100.0 + second * gain,
                0.1,
                base_ms + second * 1000,
            )

    details = engine.relative_strength_snapshot(
        "KRW-T00", int(base_ms / 1000) + 305
    )

    assert details["relative_strength_ready"] is True
    assert details["relative_strength_eligible"] is True
    assert details["relative_strength_rank"] == 1
    assert details["relative_strength_universe"] == 20


def test_default_monitor_config_uses_accuracy_first_relative_strength(monkeypatch):
    monkeypatch.delenv("MONITOR_RELATIVE_STRENGTH_TOP_PERCENT", raising=False)
    monkeypatch.delenv("MONITOR_RELATIVE_STRENGTH_MIN_5M_PCT", raising=False)

    config = MonitorConfig.from_env()

    assert config.relative_strength_top_percent == 10.0
    assert config.relative_strength_min_5m_pct == 1.0


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
    assert dispatcher.candidate_min_score == 90
    assert dispatcher.daily_candidate_min == 5
    assert dispatcher.daily_candidate_max == 10
    assert dispatcher.inactivity_status_enabled is True
    assert dispatcher.inactivity_status_seconds == 3600


def test_inactivity_status_reports_screening_without_creating_candidate(monkeypatch):
    clock = {"now": 1_000.0}
    monkeypatch.setattr("monitor.time.monotonic", lambda: clock["now"])
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "test-token")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "test-chat")
    monkeypatch.setenv("TELEGRAM_SEND_OBSERVATION_ALERTS", "false")
    monkeypatch.setenv("TELEGRAM_SEND_CANDIDATE_ALERTS", "true")
    monkeypatch.setenv("TELEGRAM_SEND_INACTIVITY_STATUS", "true")
    monkeypatch.setenv("TELEGRAM_INACTIVITY_STATUS_SECONDS", "3600")
    dispatcher = AlertDispatcher()
    MONITOR_STATE.connected = True

    asyncio.run(
        dispatcher.send(
            {
                "market": "KRW-NEAR",
                "signal": "consolidation_rebreakout",
            }
        )
    )
    dispatcher.record_candidate_rejection(
        ["완료봉 종가 위치 다소 약함(0.50)", "윗꼬리 주의(0.50)"]
    )
    clock["now"] = 4_600.0
    sent = []

    class Response:
        def raise_for_status(self):
            return None

    class Client:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

        async def post(self, url, json):
            sent.append(json)
            return Response()

    monkeypatch.setattr("monitor.httpx.AsyncClient", Client)

    assert asyncio.run(dispatcher.send_inactivity_status_if_due()) is True
    assert asyncio.run(dispatcher.send_inactivity_status_if_due()) is False
    assert len(sent) == 1
    text = sent[0]["text"]
    assert "[운영상태 | 최근 60분]" in text
    assert "원시 상승신호: 1건" in text
    assert "심층검증 탈락: 1건" in text
    assert "조건부 진입 후보: 0건" in text
    assert "매수 신호가 아닙니다" in text


def test_delivered_candidate_resets_inactivity_clock(monkeypatch):
    clock = {"now": 1_000.0}
    monkeypatch.setattr("monitor.time.monotonic", lambda: clock["now"])
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "test-token")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "test-chat")
    monkeypatch.setenv("TELEGRAM_SEND_CANDIDATE_ALERTS", "true")
    monkeypatch.setenv("TELEGRAM_CANDIDATE_MIN_TARGET_2_PCT", "5")
    monkeypatch.setenv("TELEGRAM_SEND_INACTIVITY_STATUS", "true")
    monkeypatch.setenv("TELEGRAM_INACTIVITY_STATUS_SECONDS", "3600")
    dispatcher = AlertDispatcher()

    class Response:
        def raise_for_status(self):
            return None

    class Client:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

        async def post(self, url, json):
            return Response()

    monkeypatch.setattr("monitor.httpx.AsyncClient", Client)
    clock["now"] = 4_601.0
    asyncio.run(
        dispatcher.send_candidate(
            {
                "market": "KRW-ONDO",
                "score": 97,
                "current_price": 481.0,
                "entry_low": 479.0,
                "entry_high": 481.0,
                "chase_limit": 483.0,
                "stop_price": 476.0,
                "target_1": 495.0,
                "target_2": 506.0,
                "target_1_pct": 2.9,
                "target_2_pct": 5.2,
                "target_mode": "균형 위험비형",
                "resistance_room_pct": 5.0,
                "risk_reward": 4.81,
                "suggested_position_pct": 5,
                "valid_seconds": 300,
                "risk_notes": [],
                "reasons": ["30분 횡보 상단 재돌파"],
            }
        )
    )

    clock["now"] = 8_000.0
    assert asyncio.run(dispatcher.send_inactivity_status_if_due()) is False


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


def _telegram_candidate(*, market="KRW-IQ", availability_tier=False):
    return {
        "market": market,
        "signal": "entry_candidate",
        "score": 86 if availability_tier else 95,
        "current_price": 100.0,
        "entry_low": 99.0,
        "entry_high": 100.0,
        "chase_limit": 101.0,
        "stop_price": 97.0,
        "target_1": 103.0,
        "target_2": 105.0,
        "target_1_pct": 3.0,
        "target_2_pct": 5.0,
        "target_mode": "균형 위험비형",
        "resistance_room_pct": 5.0,
        "risk_reward": 2.0,
        "suggested_position_pct": 5 if availability_tier else 15,
        "valid_seconds": 300,
        "risk_notes": [],
        "reasons": ["완료 1분봉 돌파 확정"],
        "availability_tier": availability_tier,
    }


def test_daily_candidate_max_caps_all_telegram_candidates(monkeypatch):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "test-token")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "test-chat")
    monkeypatch.setenv("TELEGRAM_DAILY_CANDIDATE_MIN", "1")
    monkeypatch.setenv("TELEGRAM_DAILY_CANDIDATE_MAX", "2")
    dispatcher = AlertDispatcher()
    sent = []

    class Response:
        def raise_for_status(self):
            return None

    class Client:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

        async def post(self, url, json):
            sent.append(json)
            return Response()

    monkeypatch.setattr("monitor.httpx.AsyncClient", Client)

    assert asyncio.run(dispatcher.send_candidate(_telegram_candidate(market="KRW-A")))
    assert asyncio.run(dispatcher.send_candidate(_telegram_candidate(market="KRW-B")))
    assert not asyncio.run(
        dispatcher.send_candidate(_telegram_candidate(market="KRW-C"))
    )
    assert len(sent) == 2
    assert MONITOR_STATE.snapshot()["candidate_delivery_count_today"] == 2


def test_availability_candidates_are_paced_and_stop_at_daily_minimum(monkeypatch):
    clock = {"now": 1_000.0}
    monkeypatch.setattr("monitor.time.monotonic", lambda: clock["now"])
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "test-token")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "test-chat")
    monkeypatch.setenv("TELEGRAM_DAILY_CANDIDATE_MIN", "2")
    monkeypatch.setenv("TELEGRAM_DAILY_CANDIDATE_MAX", "4")
    monkeypatch.setenv("TELEGRAM_AVAILABILITY_MIN_INTERVAL_SECONDS", "5400")
    monkeypatch.setenv("TELEGRAM_CANDIDATE_MIN_SCORE", "0")
    dispatcher = AlertDispatcher()
    sent = []

    class Response:
        def raise_for_status(self):
            return None

    class Client:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

        async def post(self, url, json):
            sent.append(json)
            return Response()

    monkeypatch.setattr("monitor.httpx.AsyncClient", Client)

    assert asyncio.run(
        dispatcher.send_candidate(
            _telegram_candidate(market="KRW-A", availability_tier=True)
        )
    )
    clock["now"] = 2_000.0
    assert not asyncio.run(
        dispatcher.send_candidate(
            _telegram_candidate(market="KRW-B", availability_tier=True)
        )
    )
    assert asyncio.run(dispatcher.send_candidate(_telegram_candidate(market="KRW-C")))
    clock["now"] = 7_000.0
    assert not asyncio.run(
        dispatcher.send_candidate(
            _telegram_candidate(market="KRW-D", availability_tier=True)
        )
    )
    assert len(sent) == 2


def test_availability_candidate_is_labeled_in_telegram_message():
    text = _candidate_text(_telegram_candidate(availability_tier=True))

    assert "[조건부 진입 후보 | 보완형 | 조건점수 86/100]" in text


def test_dispatch_revalidation_rejects_stale_entry_and_refreshes_metrics():
    candidate = {
        "entry_low": 100.0,
        "entry_high": 101.0,
        "chase_limit": 102.0,
        "stop_price": 98.0,
        "target_1": 104.0,
        "target_2": 106.0,
        "resistance_price": 105.0,
        "current_price": 100.0,
    }

    rejected, reason = _revalidate_candidate_for_dispatch(candidate, 101.5)
    assert rejected is None
    assert "진입구간 밖" in str(reason)

    refreshed, reason = _revalidate_candidate_for_dispatch(candidate, 100.5)
    assert reason is None
    assert refreshed is not None
    assert refreshed["current_price"] == 100.5
    assert refreshed["entry_reference_price"] == 100.5
    assert refreshed["target_2_pct"] == round((106.0 / 100.5 - 1) * 100, 2)


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


def test_six_hour_trend_track_protects_entry_after_target_one(monkeypatch):
    monkeypatch.setenv("ENABLE_CANDIDATE_ANALYSIS", "true")
    analyzer = CandidateAnalyzer(CandidateConfig.from_env(), AlertDispatcher())
    MONITOR_STATE.trend_outcomes.clear()
    analyzer._start_trend_track(
        {
            "market": "KRW-IQ",
            "source_signal": "breakout",
            "entry_reference_price": 100.0,
            "stop_price": 97.0,
            "target_1": 103.0,
            "target_2": 108.0,
            "score": 95,
            "target_mode": "상대강도 추세추적형",
            "relative_strength_rank": 2,
            "relative_strength_universe": 100,
            "relative_strength_percentile": 2.0,
            "momentum_5m_pct": 2.0,
            "momentum_15m_pct": 4.0,
            "momentum_60m_pct": 6.0,
        },
        1_800_000_000.0,
    )

    analyzer._observe_trend_track("KRW-IQ", 103.0, 1_800_000_120.0)
    analyzer._observe_trend_track("KRW-IQ", 100.0, 1_800_000_240.0)

    performance = MONITOR_STATE.candidate_performance()[
        "six_hour_trend_performance"
    ]
    assert performance["sample_count"] == 1
    assert performance["target_1_reached"] == 1
    assert performance["protected_after_target_1"] == 1
    assert performance["recent"][0]["relative_strength_rank"] == 2
    assert "KRW-IQ" not in analyzer._trend_tracks


def test_six_hour_trend_track_records_ten_fifteen_twenty_percent(monkeypatch):
    monkeypatch.setenv("ENABLE_CANDIDATE_ANALYSIS", "true")
    analyzer = CandidateAnalyzer(CandidateConfig.from_env(), AlertDispatcher())
    MONITOR_STATE.trend_outcomes.clear()
    analyzer._start_trend_track(
        {
            "market": "KRW-IQ",
            "source_signal": "breakout",
            "entry_reference_price": 100.0,
            "stop_price": 97.0,
            "target_1": 103.0,
            "target_2": 110.0,
            "target_3": 115.0,
            "target_4": 120.0,
            "score": 96,
            "target_mode": "상대강도 추세추적형",
        },
        1_800_000_000.0,
    )

    analyzer._observe_trend_track("KRW-IQ", 115.0, 1_800_000_300.0)
    assert "KRW-IQ" in analyzer._trend_tracks
    analyzer._observe_trend_track("KRW-IQ", 120.0, 1_800_000_600.0)

    performance = MONITOR_STATE.candidate_performance()[
        "six_hour_trend_performance"
    ]
    assert performance["target_2_reached"] == 1
    assert performance["target_3_reached"] == 1
    assert performance["target_4_reached"] == 1
    assert performance["recent"][0]["result"] == "target_4_reached"


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
    assert "조건점수는 적중 확률이 아닙니다" in text


def test_candidate_message_displays_fifteen_and_twenty_percent_extensions():
    candidate = _telegram_candidate()
    candidate.update(
        {
            "target_2": 110.0,
            "target_2_pct": 10.0,
            "target_3": 115.0,
            "target_3_pct": 15.0,
            "target_4": 120.0,
            "target_4_pct": 20.0,
            "target_mode": "상대강도 추세추적형",
        }
    )

    text = _candidate_text(candidate)

    assert "추세 확장 관찰:" in text
    assert "(+15%)" in text
    assert "(+20%)" in text
