import asyncio
from datetime import datetime, timedelta, timezone

from candidate_analysis import CandidateConfig
from monitor import (
    MONITOR_STATE,
    AlertDispatcher,
    CandidateAnalyzer,
    MonitorConfig,
    SignalEngine,
    _candidate_text,
    _daily_performance_text,
    _early_watch_text,
    _management_text,
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
        "preleader_enabled": False,
        "preleader_start_minute_kst": 525,
        "preleader_end_minute_kst": 555,
        "preleader_check_interval_seconds": 15,
        "preleader_min_value_ratio_10m": 2.0,
        "preleader_min_value_ratio_30m": 1.5,
        "preleader_min_3m_pct": 0.4,
        "preleader_min_5m_pct": 0.6,
        "preleader_max_percentile": 20.0,
        "preleader_rank_improvement_pct": 5.0,
        "preleader_cooldown_seconds": 1800,
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

    surge = next(alert for alert in alerts if alert["signal"] == "price_volume_surge")
    assert surge["confirmation_started_at_utc"] < surge["time_utc"]


def test_rest_warm_start_restores_relative_strength_immediately():
    engine = SignalEngine(_config(relative_strength_min_universe=20))
    now = 1_800_000_000
    boundary = now - now % 60

    for market_index in range(20):
        candles = []
        for age in range(1, 66):
            opened = boundary - age * 60
            oldest_index = 65 - age
            gain = 0.08 if market_index == 0 else 0.001 * market_index
            close = 100 + oldest_index * gain
            candles.append(
                {
                    "candle_date_time_utc": datetime.fromtimestamp(
                        opened, tz=timezone.utc
                    ).isoformat(),
                    "opening_price": close - gain,
                    "high_price": close + 0.01,
                    "low_price": close - 0.01,
                    "trade_price": close,
                    "candle_acc_trade_price": 10_000.0,
                }
            )
        assert engine.warm_market(f"KRW-T{market_index}", candles, now=now)

    snapshot = engine.relative_strength_snapshot("KRW-T0", now - 1)

    assert snapshot["relative_strength_ready"] is True
    assert snapshot["relative_strength_rank"] == 1
    assert snapshot["relative_strength_universe"] == 20
    assert snapshot["market_regime"] in {"risk_on", "neutral", "risk_off"}


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
    monkeypatch.delenv("MONITOR_PRELEADER_ENABLED", raising=False)
    monkeypatch.delenv("MONITOR_PRELEADER_START_KST", raising=False)
    monkeypatch.delenv("MONITOR_PRELEADER_END_KST", raising=False)

    config = MonitorConfig.from_env()

    assert config.relative_strength_top_percent == 10.0
    assert config.relative_strength_min_5m_pct == 1.0
    assert config.preleader_enabled is True
    assert config.preleader_start_minute_kst == 8 * 60 + 45
    assert config.preleader_end_minute_kst == 9 * 60 + 15


def test_preleader_detects_volume_acceleration_before_large_one_minute_move():
    engine = SignalEngine(
        _config(
            price_surge_1m_pct=100.0,
            breakout_pct=100.0,
            rebreakout_enabled=False,
            relative_strength_min_universe=1,
            relative_strength_top_percent=100.0,
            preleader_enabled=True,
            preleader_max_percentile=100.0,
            min_trade_value_krw=100.0,
        )
    )
    kst = timezone(timedelta(hours=9))
    base = int(datetime(2026, 9, 15, 8, 14, tzinfo=kst).timestamp())
    alerts = []

    for second in range(2161):
        accelerating = second >= 1860
        price = 100.0 + max(0, second - 1860) * 0.003
        volume = 0.5 if accelerating else 0.1
        alerts.extend(
            engine.update("KRW-IQ", price, volume, (base + second) * 1000)
        )

    preleaders = [
        alert
        for alert in alerts
        if alert["signal"] == "leader_volume_acceleration"
    ]
    assert len(preleaders) == 1
    assert preleaders[0]["internal_only"] is True
    assert preleaders[0]["change_1m_pct"] < 1.5
    assert preleaders[0]["preleader_volume_ratio_10m"] >= 2.0
    assert preleaders[0]["preleader_volume_ratio_30m"] >= 1.5
    assert preleaders[0]["momentum_3m_pct"] >= 0.4


def test_preleader_signal_is_kept_internal_for_first_pullback(monkeypatch):
    monkeypatch.setenv("ENABLE_CANDIDATE_ANALYSIS", "true")
    analyzer = CandidateAnalyzer(CandidateConfig.from_env(), AlertDispatcher())
    alert = {
        "time_utc": "2026-09-14T23:50:00+00:00",
        "market": "KRW-IQ",
        "signal": "leader_volume_acceleration",
        "price": 100.0,
        "relative_strength_rank": 3,
        "relative_strength_universe": 100,
        "relative_strength_percentile": 3.0,
        "momentum_3m_pct": 0.6,
        "momentum_5m_pct": 0.8,
        "preleader_volume_ratio_10m": 2.5,
        "preleader_volume_ratio_30m": 2.0,
        "internal_only": True,
    }

    assert analyzer.watch_preleader(alert) is True

    state = analyzer._watchlist["KRW-IQ"]
    assert state["source_signal"] == "leader_volume_acceleration"
    assert state["leader_peak_price"] == 100.0
    assert state["leader_pullback_seen"] is False
    assert "첫 눌림 대기" in state["last_rejected"][0]


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


def test_management_message_does_not_assume_user_entered():
    text = _management_text(
        {
            "market": "KRW-IQ",
            "event": "target_1",
            "entry_price": 100.0,
            "current_price": 103.0,
        }
    )

    assert "1차 목표 도달" in text
    assert "진입했다면" in text
    assert "실제 체결 여부를 알 수 없는" in text


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


def test_rejected_leader_is_rechecked_after_pullback_and_reclaim(monkeypatch):
    monkeypatch.setenv("ENABLE_CANDIDATE_ANALYSIS", "true")

    def relative_strength(_market, _now):
        return {
            "relative_strength_ready": True,
            "relative_strength_eligible": True,
            "relative_strength_rank": 2,
            "relative_strength_universe": 100,
            "relative_strength_percentile": 2.0,
            "momentum_5m_pct": 2.4,
            "momentum_15m_pct": 5.0,
            "momentum_60m_pct": 8.0,
            "early_trend": True,
        }

    analyzer = CandidateAnalyzer(
        CandidateConfig.from_env(), AlertDispatcher(), relative_strength
    )
    analyzer._watchlist["KRW-IQ"] = {
        "market": "KRW-IQ",
        "created_at": 1_800_000_000.0,
        "first_signal_time_utc": "2026-09-12T10:00:00+00:00",
        "first_signal_price": 100.0,
        "source_signal": "breakout",
        "breakout_level": 100.0,
        "leader_peak_price": 110.0,
        "leader_pullback_seen": False,
        "leader_recheck_count": 0,
        "recheck_count": 0,
        "expires_at": 9_999_999_999.0,
    }
    seen = []

    async def fake_analyze(alert):
        seen.append(alert)

    monkeypatch.setattr(analyzer, "_analyze", fake_analyze)

    async def run():
        assert analyzer._observe_leader_watchlist(
            "KRW-IQ", 108.5, 1_800_000_100.0
        ) is False
        assert analyzer._observe_leader_watchlist(
            "KRW-IQ", 109.1, 1_800_000_110.0
        ) is True
        await asyncio.sleep(0)

    asyncio.run(run())

    assert len(seen) == 1
    assert seen[0]["leader_pullback_recheck"] is True
    assert seen[0]["pullback_retest"] is True
    assert seen[0]["relative_strength_rank"] == 2
    assert seen[0]["breakout_level"] > 108.5


def test_pullback_reclaim_is_not_rechecked_after_leadership_is_lost(monkeypatch):
    monkeypatch.setenv("ENABLE_CANDIDATE_ANALYSIS", "true")
    analyzer = CandidateAnalyzer(
        CandidateConfig.from_env(),
        AlertDispatcher(),
        lambda _market, _now: {
            "relative_strength_ready": True,
            "relative_strength_eligible": False,
            "relative_strength_rank": 40,
            "relative_strength_universe": 100,
            "relative_strength_percentile": 40.0,
            "early_trend": False,
        },
    )
    analyzer._watchlist["KRW-IQ"] = {
        "market": "KRW-IQ",
        "created_at": 1_800_000_000.0,
        "first_signal_time_utc": "2026-09-12T10:00:00+00:00",
        "first_signal_price": 100.0,
        "source_signal": "breakout",
        "leader_peak_price": 110.0,
        "leader_pullback_seen": True,
        "leader_pullback_low": 108.0,
        "leader_recheck_count": 0,
        "expires_at": 9_999_999_999.0,
    }

    async def run():
        assert analyzer._observe_leader_watchlist(
            "KRW-IQ", 108.6, 1_800_000_100.0
        ) is False

    asyncio.run(run())
    assert "KRW-IQ" not in analyzer._inflight_markets


def test_deep_leader_pullback_stays_invalid_until_a_fresh_high(monkeypatch):
    monkeypatch.setenv("ENABLE_CANDIDATE_ANALYSIS", "true")
    analyzer = CandidateAnalyzer(
        CandidateConfig.from_env(),
        AlertDispatcher(),
        lambda _market, _now: {
            "relative_strength_ready": True,
            "relative_strength_eligible": True,
            "relative_strength_rank": 1,
            "relative_strength_universe": 100,
            "relative_strength_percentile": 1.0,
            "early_trend": True,
        },
    )
    analyzer._watchlist["KRW-IQ"] = {
        "market": "KRW-IQ",
        "created_at": 1_800_000_000.0,
        "source_signal": "breakout",
        "leader_peak_price": 110.0,
        "leader_pullback_seen": False,
        "leader_recheck_count": 0,
        "expires_at": 9_999_999_999.0,
    }

    async def run():
        assert analyzer._observe_leader_watchlist(
            "KRW-IQ", 104.0, 1_800_000_100.0
        ) is False
        assert analyzer._observe_leader_watchlist(
            "KRW-IQ", 105.0, 1_800_000_110.0
        ) is False

    asyncio.run(run())
    assert analyzer._watchlist["KRW-IQ"]["leader_pullback_invalidated"] is True
    assert "KRW-IQ" not in analyzer._inflight_markets


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


def test_candidate_message_labels_leader_pullback_recheck():
    candidate = _telegram_candidate()
    candidate.update(
        {
            "is_reentry": True,
            "leader_pullback_recheck": True,
        }
    )

    text = _candidate_text(candidate)

    assert "[조건부 진입 후보 | 선도주 눌림 |" in text


def test_candidate_message_labels_early_leader_lane():
    candidate = _telegram_candidate()
    candidate["selection_lane"] = "early_leader"

    text = _candidate_text(candidate)

    assert "[조건부 진입 후보 | 선도주 정밀형 |" in text


def test_early_watch_is_explicitly_not_an_entry_candidate():
    text = _early_watch_text(
        {
            "market": "KRW-ARK",
            "price": 100.0,
            "momentum_3m_pct": 0.8,
            "momentum_5m_pct": 1.2,
            "preleader_volume_ratio_10m": 3.0,
            "preleader_volume_ratio_30m": 2.0,
            "relative_strength_rank": 3,
            "relative_strength_universe": 180,
        }
    )

    assert "[초기 포착 | 진입 검증 전]" in text
    assert "아직 매수 후보가 아닙니다" in text
    assert "3/180위" in text


def test_daily_performance_text_separates_simulation_from_real_returns():
    text = _daily_performance_text(
        {
            "day_kst": "2026-09-15",
            "accepted_count": 3,
            "unique_markets": 3,
            "target_1_first": 1,
            "stop_first": 1,
            "expired": 1,
            "target_rate_pct": 50.0,
            "simulated_return_average_pct": 0.2,
            "assumed_cost_pct_per_candidate": 0.2,
            "raw_decided_count": 5,
            "raw_target_first": 2,
            "missed_raw_winners": 1,
        }
    )

    assert "후보당 모의 평균: +0.20%" in text
    assert "실제 계좌 수익이 아닌" in text


def test_recently_delivered_market_is_not_scheduled_again(monkeypatch):
    monkeypatch.setenv("ENABLE_CANDIDATE_ANALYSIS", "true")
    monkeypatch.setenv("CANDIDATE_REPEAT_COOLDOWN_SECONDS", "14400")
    monkeypatch.setattr("monitor.time.time", lambda: 1_800_000_000.0)
    analyzer = CandidateAnalyzer(CandidateConfig.from_env(), AlertDispatcher())
    analyzer._last_delivered_at["KRW-XLM"] = 1_799_999_000.0

    scheduled = analyzer.schedule(
        {
            "time_utc": "2026-09-15T00:00:00+00:00",
            "market": "KRW-XLM",
            "signal": "breakout",
            "price": 267.0,
        }
    )

    assert scheduled is False
    assert "KRW-XLM" not in analyzer._inflight_markets


def test_stronger_signal_upgrades_inflight_candidate(monkeypatch):
    monkeypatch.setenv("ENABLE_CANDIDATE_ANALYSIS", "true")
    analyzer = CandidateAnalyzer(CandidateConfig.from_env(), AlertDispatcher())
    analyzer._watchlist["KRW-LSK"] = {
        "market": "KRW-LSK",
        "created_at": 1_800_000_000.0,
        "first_signal_time_utc": "2026-09-16T00:01:20+00:00",
        "first_signal_price": 342.0,
        "expires_at": 9_999_999_999.0,
    }
    entered = asyncio.Event()
    release = asyncio.Event()
    seen = []

    async def fake_analyze(alert):
        entered.set()
        await release.wait()
        seen.append(dict(alert))

    monkeypatch.setattr(analyzer, "_analyze", fake_analyze)

    async def run():
        assert analyzer.schedule(
            {
                "time_utc": "2026-09-16T00:07:34+00:00",
                "market": "KRW-LSK",
                "signal": "breakout",
                "price": 343.0,
                "breakout_level": 342.8,
                "relative_strength_percentile": 4.8,
            }
        )
        await entered.wait()
        assert analyzer.schedule(
            {
                "time_utc": "2026-09-16T00:07:45+00:00",
                "market": "KRW-LSK",
                "signal": "consolidation_rebreakout",
                "price": 345.0,
                "breakout_level": 343.026,
                "relative_strength_percentile": 4.59,
                "relative_strength_rank": 10,
                "relative_strength_universe": 218,
            }
        )
        release.set()
        await asyncio.sleep(0)
        await asyncio.sleep(0)

    asyncio.run(run())

    assert len(seen) == 1
    assert seen[0]["signal"] == "consolidation_rebreakout"
    assert seen[0]["superseded_signal"] == "breakout"
    assert seen[0]["watchlist_recheck"] is True
    assert seen[0]["is_reentry"] is True
    assert seen[0]["breakout_level"] == 343.026
    assert seen[0]["best_relative_strength_percentile"] == 4.59
    assert "KRW-LSK" not in analyzer._inflight_markets
    assert "KRW-LSK" not in analyzer._inflight_alerts


def test_daily_performance_counts_cost_adjusted_outcomes():
    MONITOR_STATE.candidate_outcomes.clear()
    MONITOR_STATE.signal_outcomes.clear()
    MONITOR_STATE.screening_records.clear()
    MONITOR_STATE.candidate_outcomes.extend(
        [
            {
                "market": "KRW-A",
                "result": "target_1_first",
                "entry_price": 100.0,
                "exit_price": 103.0,
                "completed_at_utc": "2026-09-15T01:00:00+00:00",
            },
            {
                "market": "KRW-B",
                "result": "stop_first",
                "entry_price": 100.0,
                "exit_price": 98.0,
                "completed_at_utc": "2026-09-15T02:00:00+00:00",
            },
        ]
    )
    MONITOR_STATE.screening_records.extend(
        [
            {
                "market": "KRW-A",
                "decision": "accepted",
                "time_utc": "2026-09-15T00:30:00+00:00",
            },
            {
                "market": "KRW-B",
                "decision": "accepted",
                "time_utc": "2026-09-15T01:30:00+00:00",
            },
        ]
    )

    report = MONITOR_STATE.daily_performance("2026-09-15", cost_pct=0.2)

    assert report["accepted_count"] == 2
    assert report["target_1_first"] == 1
    assert report["stop_first"] == 1
    assert report["target_rate_pct"] == 50.0
    assert report["simulated_return_average_pct"] == 0.3
