import asyncio
import logging
from datetime import datetime, timedelta, timezone

import monitor
import pytest


def test_refresh_adds_new_market_removes_old_and_preserves_engine_state(monkeypatch):
    import json
    engine = SignalEngine(_config(all_krw_markets=True))
    engine.active_markets = {"KRW-BTC", "KRW-OLD"}
    sentinel = engine._windows["KRW-BTC"]
    async def resolve(config): return ["KRW-POD", "KRW-BTC"]
    monkeypatch.setattr(monitor, "_resolve_markets", resolve)
    class Socket:
        def __init__(self): self.sent = []
        async def send(self, data): self.sent.append(json.loads(data))
    socket = Socket()
    refreshed, added = asyncio.run(monitor._refresh_subscription_once(
        socket, engine.config, engine, ["KRW-BTC", "KRW-OLD"]))
    assert refreshed == ["KRW-BTC", "KRW-POD"] and added == ["KRW-POD"]
    assert socket.sent[0][1]["codes"] == refreshed
    assert socket.sent[0][1]["is_only_realtime"]
    assert engine.active_markets == set(refreshed)
    assert engine._windows["KRW-BTC"] is sentinel
    assert MONITOR_STATE.snapshot()["market_count"] == 2
    asyncio.run(monitor._refresh_subscription_once(socket, engine.config, engine, refreshed))
    assert len(socket.sent) == 1  # No unnecessary resubscription.


@pytest.mark.parametrize("cause", ["empty", "rest_failure", "send_failure"])
def test_market_refresh_failure_does_not_replace_live_membership(monkeypatch, cause):
    engine = SignalEngine(_config(all_krw_markets=True))
    engine.active_markets = {"KRW-BTC"}
    async def resolve(config):
        if cause == "rest_failure": raise RuntimeError("public API unavailable")
        return [] if cause == "empty" else ["KRW-BTC", "KRW-POD"]
    monkeypatch.setattr(monitor, "_resolve_markets", resolve)
    class Socket:
        async def send(self, data): raise RuntimeError("send failed")
    with pytest.raises(RuntimeError):
        asyncio.run(monitor._refresh_subscription_once(Socket(), engine.config, engine, ["KRW-BTC"]))
    assert engine.active_markets == {"KRW-BTC"}


def test_periodic_refresh_warms_only_additions_and_cancels_cleanly(monkeypatch):
    engine = SignalEngine(_config(all_krw_markets=True))
    resolutions = iter([["KRW-BTC", "KRW-POD"], ["KRW-BTC", "KRW-POD"]])
    async def resolve(config): return next(resolutions)
    monkeypatch.setattr(monitor, "_resolve_markets", resolve)
    warmed, sends = [], []
    async def warm(engine, codes): warmed.extend(codes)
    monkeypatch.setattr(monitor, "_warm_signal_engine", warm)
    original_sleep = asyncio.sleep
    async def sleep(seconds): await original_sleep(0)
    monkeypatch.setattr(monitor.asyncio, "sleep", sleep)
    class Socket:
        async def send(self, data): sends.append(data)
    async def run():
        tasks, markets = set(), ["KRW-BTC"]
        task = asyncio.create_task(monitor._refresh_market_subscriptions(Socket(), engine.config, engine, markets, tasks))
        for _ in range(4): await original_sleep(0)
        task.cancel()
        await asyncio.gather(task, *tasks, return_exceptions=True)
        assert markets == ["KRW-BTC", "KRW-POD"]
    asyncio.run(run())
    assert warmed == ["KRW-POD"] and len(sends) == 1


def test_young_completed_breakout_is_admitted_to_recheck_without_hourly_history(monkeypatch):
    monkeypatch.setenv("ENABLE_CANDIDATE_ANALYSIS", "true")
    monkeypatch.setattr(MONITOR_STATE, "has_recent_signal", lambda *args: False)
    relative = {"relative_strength_ready": True, "relative_strength_eligible": True,
                "relative_strength_percentile": 1.5, "momentum_5m_pct": 1.1,
                "momentum_15m_pct": 2.0, "momentum_60m_pct": None}
    analyzer = CandidateAnalyzer(CandidateConfig.from_env(), AlertDispatcher(), lambda *args: relative)
    analyzer._watchlist["KRW-TEST"] = {"created_at": 1_800_000_000,
        "expires_at": 1_800_010_000, "source_signal": "breakout", "breakout_level": 100,
        "first_signal_price": 100, "last_checked_at": 1_800_000_295}
    seen = []
    async def analyze(alert): seen.append(alert)
    monkeypatch.setattr(analyzer, "_analyze", analyze)
    async def run():
        assert analyzer._observe_completed_bar_watchlist("KRW-TEST", 101, 1_800_000_305)
        await asyncio.sleep(0)
        await asyncio.sleep(0)
        # v3.19 intentionally no longer uses the generic +1% eligibility
        # flag for a fresh completed breakout. It must still fail when the
        # dedicated 15m leadership proof drops below its +0.8% floor.
        relative["relative_strength_eligible"] = False
        relative["momentum_15m_pct"] = 0.7
        assert not analyzer._observe_completed_bar_watchlist("KRW-TEST", 101, 1_800_000_605)
    asyncio.run(run())
    assert len(seen) == 1 and seen[0]["completed_breakout_recheck"]
    assert not seen[0]["hourly_recheck"]
    assert not seen[0].get("is_reentry") and not seen[0].get("pullback_retest")


def test_breadth_exception_requires_renewed_rank_eligibility_and_market_floor():
    analyzer = CandidateAnalyzer(CandidateConfig.from_env(), AlertDispatcher())
    candidate = {"relative_strength_ready": True, "breadth_breakout_exception": True}
    relative = {"relative_strength_ready": True, "relative_strength_eligible": True,
                "relative_strength_percentile": 1.5, "momentum_5m_pct": 1.1,
                "momentum_15m_pct": 2, "market_breadth_5m_pct": 32.5}
    assert analyzer._relative_survival_rejections(candidate, relative) == []
    relative.update(relative_strength_eligible=False, market_breadth_5m_pct=10,
                    relative_strength_percentile=3)
    reasons = analyzer._relative_survival_rejections(candidate, relative)
    assert len(reasons) == 3


def test_coverage_distinguishes_subscription_from_received_trades():
    state = monitor.MonitorState()
    state.mark_started(2, "telegram", ["KRW-BTC", "KRW-POD"])
    state.mark_trade_received("KRW-BTC")
    assert state.snapshot()["markets_without_trade"] == ["KRW-POD"]
    state.mark_market_refresh(2, True, ["KRW-BTC", "KRW-NEW"])
    assert state.snapshot()["received_market_count"] == 1
    assert state.snapshot()["markets_without_trade"] == ["KRW-NEW"]
    state.mark_trade_received("KRW-NEW")
    assert state.snapshot()["received_market_count"] == 2


def test_removed_market_is_excluded_from_relative_universe():
    from collections import deque
    engine = SignalEngine(_config(relative_strength_min_universe=1))
    now = 1_800_000_300
    engine._momentum_prices["KRW-BTC"] = deque([(now - 300, 100), (now, 101)])
    engine._momentum_prices["KRW-OLD"] = deque([(now - 300, 100), (now, 150)])
    engine.active_markets = {"KRW-BTC"}
    result = engine.relative_strength_snapshot("KRW-BTC", now)
    assert result["relative_strength_universe"] == 1
    assert result["relative_strength_rank"] == 1


def test_screening_audit_updates_live_price_and_excludes_unfinished_candle():
    now = datetime(2026, 10, 1, 10, 0, 5, tzinfo=timezone.utc)
    alert = {"signal_id": "same", "market": "KRW-QKC", "price": 4.1}
    snapshot = {"as_of": now, "ticker": {"trade_price": 4.2, "timestamp": 123}}
    for minutes in (1, 5, 15):
        snapshot[f"candles_{minutes}m"] = [
            {"candle_date_time_utc": now.replace(second=0).isoformat()},
            {"candle_date_time_utc": (now.replace(second=0) - timedelta(minutes=minutes)).isoformat()},
        ]
    monitor.CandidateAnalyzer._record_screening_snapshot(alert, snapshot, "entry_screen")
    before = monitor.CandidateAnalyzer._screening_record(alert, "rejected", ["test"])
    snapshot["ticker"] = {"trade_price": 4.3, "timestamp": 456}
    monitor.CandidateAnalyzer._record_screening_snapshot(alert, snapshot, "survival_screen")
    after = monitor.CandidateAnalyzer._screening_record(alert, "survival_rejected", ["test"])
    assert before["screening_snapshot"]["current_price"] == 4.2
    assert after["screening_snapshot"]["current_price"] == 4.3
    assert after["signal_price"] == 4.1
    assert after["screening_snapshot"]["completed_candle_start_times_utc"]["5"] == "2026-10-01T09:55:00+00:00"
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
        "extended_leader_enabled": False,
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
        "persistent_leader_enabled": False,
        "persistent_leader_check_interval_seconds": 30,
        "persistent_leader_min_5m_pct": 0.8,
        "persistent_leader_min_15m_pct": 3.0,
        "persistent_leader_min_60m_pct": 5.0,
        "persistent_leader_min_value_ratio_10m": 1.25,
        "persistent_leader_min_value_ratio_30m": 1.15,
        "persistent_leader_max_percentile": 3.0,
        "persistent_leader_cooldown_seconds": 1800,
        "pullback_watch_enabled": False,
        "pullback_watch_min_1m_pct": 0.8,
        "pullback_watch_min_volume_ratio": 1.3,
        "pullback_watch_max_percentile": 15.0,
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


def test_moderate_impulse_is_kept_as_internal_pullback_watch():
    engine = SignalEngine(
        _config(
            price_surge_1m_pct=1.5,
            breakout_pct=100.0,
            rebreakout_enabled=False,
            pullback_watch_enabled=True,
            min_trade_value_krw=100.0,
        )
    )
    engine.relative_strength_snapshot = lambda market, now: {
        "relative_strength_ready": True,
        "relative_strength_eligible": True,
        "relative_strength_rank": 5,
        "relative_strength_universe": 100,
        "relative_strength_percentile": 5.0,
        "momentum_5m_pct": 1.2,
        "momentum_15m_pct": 2.0,
        "early_trend": True,
    }
    alerts = []
    base_ms = 1_800_000_000_000
    for second in range(361):
        rising = second >= 300
        price = 100.0 + max(0, second - 300) * 0.015
        volume = 2.0 if rising else 0.1
        alerts.extend(engine.update("KRW-IQ", price, volume, base_ms + second * 1000))

    watch = next(alert for alert in alerts if alert.get("pullback_watch"))
    assert watch["internal_only"] is True
    assert watch["notify_early_watch"] is False
    assert watch["change_1m_pct"] < 1.5


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


def test_persistent_leader_detects_stair_step_without_one_minute_spike():
    engine = SignalEngine(
        _config(
            price_surge_1m_pct=100.0,
            breakout_pct=100.0,
            rebreakout_enabled=False,
            relative_strength_min_universe=20,
            persistent_leader_enabled=True,
            persistent_leader_max_percentile=5.0,
            min_trade_value_krw=1_000.0,
        )
    )
    now = 1_800_000_030
    boundary = now - now % 60

    for market_index in range(20):
        candles = []
        for age in range(1, 66):
            opened = boundary - age * 60
            close = 107.0 - age * 0.25 if market_index == 0 else 100.0
            candles.append(
                {
                    "candle_date_time_utc": datetime.fromtimestamp(
                        opened, tz=timezone.utc
                    ).isoformat(),
                    "opening_price": close - 0.02,
                    "high_price": close + 0.03,
                    "low_price": close - 0.03,
                    "trade_price": close,
                    "candle_acc_trade_price": 10_000.0,
                }
            )
        assert engine.warm_market(f"KRW-T{market_index}", candles, now=now)

    alerts = engine.update("KRW-T0", 107.0, 200.0, now * 1000)
    persistent = next(
        alert
        for alert in alerts
        if alert["signal"] == "persistent_leader_acceleration"
    )

    assert persistent["persistent_leader"] is True
    assert persistent["momentum_15m_pct"] >= 3.0
    assert persistent["momentum_60m_pct"] >= 5.0
    assert persistent["relative_strength_rank"] == 1
    assert not any(alert["signal"] == "price_volume_surge" for alert in alerts)


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
    monkeypatch.delenv("MONITOR_EXTENDED_LEADER_ENABLED", raising=False)

    config = MonitorConfig.from_env()

    assert config.relative_strength_top_percent == 10.0
    assert config.relative_strength_min_5m_pct == 1.0
    assert config.preleader_enabled is True
    assert config.preleader_start_minute_kst == 8 * 60 + 45
    assert config.preleader_end_minute_kst == 9 * 60 + 15
    assert config.extended_leader_enabled is True


def test_exceptional_volume_leader_runs_after_morning_without_early_watch():
    engine = SignalEngine(
        _config(
            price_surge_1m_pct=100.0,
            breakout_pct=100.0,
            rebreakout_enabled=False,
            preleader_enabled=True,
            extended_leader_enabled=True,
        )
    )
    engine.relative_strength_snapshot = lambda market, now: {
        "relative_strength_ready": True,
        "relative_strength_eligible": True,
        "relative_strength_rank": 2,
        "relative_strength_universe": 200,
        "relative_strength_percentile": 1.0,
        "momentum_15m_pct": 2.0,
        "early_trend": True,
        "market_regime": "neutral",
    }
    kst = timezone(timedelta(hours=9))
    base = int(datetime(2026, 9, 18, 12, 0, tzinfo=kst).timestamp())
    alerts = []
    for second in range(2050):
        speeding = second >= 1920
        price = 100 + max(0, second - 1920) * 0.035
        volume = 50_000 if speeding else 100
        alerts.extend(engine.update("KRW-IQ", price, volume, (base + second) * 1000))

    leaders = [a for a in alerts if a["signal"] == "leader_volume_acceleration"]
    assert len(leaders) == 1
    assert leaders[0]["notify_early_watch"] is True
    assert leaders[0]["internal_only"] is True
    assert leaders[0]["preleader_volume_ratio_10m"] >= 8.0
    assert leaders[0]["preleader_volume_ratio_30m"] >= 8.0


def test_telegram_transport_logging_is_reprotected_and_urls_are_redacted(monkeypatch):
    logger = logging.getLogger("httpx")
    original = logger.level
    try:
        logger.setLevel(logging.INFO)
        monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "fake-private-token")
        dispatcher = AlertDispatcher()
        assert dispatcher._telegram_url().endswith("/sendMessage")
        assert logger.level >= logging.WARNING
        for name in ("httpx", "upbit-monitor"):
            record = logging.LogRecord(
                name,
                logging.ERROR,
                "test.py",
                1,
                "request failed: %s",
                ("https://api.telegram.org/botfake-private-token/sendMessage",),
                None,
            )
            for log_filter in logging.getLogger(name).filters:
                log_filter.filter(record)
            assert "fake-private-token" not in record.getMessage()
            assert "[REDACTED]" in record.getMessage()
    finally:
        logger.setLevel(original)


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


def test_initial_price_surge_is_watched_instead_of_sent_immediately(monkeypatch):
    monkeypatch.setenv("ENABLE_CANDIDATE_ANALYSIS", "true")
    analyzer = CandidateAnalyzer(CandidateConfig.from_env(), AlertDispatcher())

    assert analyzer.schedule(
        {
            "time_utc": "2026-09-27T14:00:00+00:00",
            "market": "KRW-IQ",
            "signal": "price_volume_surge",
            "price": 100.0,
        }
    )
    assert "KRW-IQ" in analyzer._watchlist
    assert analyzer._watchlist["KRW-IQ"]["pullback_watch"] is True
    assert "KRW-IQ" not in analyzer._inflight_markets


def test_pullback_watch_rechecks_without_requiring_early_trend(monkeypatch):
    monkeypatch.setenv("ENABLE_CANDIDATE_ANALYSIS", "true")
    monkeypatch.setattr("monitor.time.time", lambda: 1_800_000_000.0)

    def relative_strength(_market, _now):
        return {
            "relative_strength_ready": True,
            "relative_strength_eligible": True,
            "relative_strength_rank": 8,
            "relative_strength_universe": 100,
            "relative_strength_percentile": 8.0,
            "momentum_5m_pct": 1.3,
            "momentum_15m_pct": 3.0,
            "early_trend": False,
        }

    analyzer = CandidateAnalyzer(
        CandidateConfig.from_env(), AlertDispatcher(), relative_strength
    )
    assert analyzer.watch_preleader(
        {
            "time_utc": "2026-09-27T14:00:00+00:00",
            "market": "KRW-IQ",
            "signal": "price_volume_surge",
            "price": 100.0,
            "pullback_watch": True,
            "notify_early_watch": False,
        }
    )
    seen = []

    async def fake_analyze(alert):
        seen.append(alert)

    monkeypatch.setattr(analyzer, "_analyze", fake_analyze)

    async def run():
        assert analyzer._observe_leader_watchlist("KRW-IQ", 99.3, 1_800_000_100.0) is False
        assert analyzer._observe_leader_watchlist("KRW-IQ", 99.7, 1_800_000_110.0) is True
        await asyncio.sleep(0)

    asyncio.run(run())
    assert seen[0]["leader_pullback_recheck"] is True
    assert seen[0]["breakout_level"] > 99.3


def test_survival_rejects_candidate_that_loses_relative_strength(monkeypatch):
    monkeypatch.setenv("ENABLE_CANDIDATE_ANALYSIS", "true")
    analyzer = CandidateAnalyzer(CandidateConfig.from_env(), AlertDispatcher())
    reasons = analyzer._relative_survival_rejections(
        {"relative_strength_ready": True},
        {
            "relative_strength_ready": True,
            "relative_strength_percentile": 22.0,
            "momentum_5m_pct": -0.2,
            "momentum_15m_pct": -0.1,
        },
    )
    assert any("상대강도 이탈" in reason for reason in reasons)
    assert any("5분 모멘텀 소멸" in reason for reason in reasons)
    assert any("15분 추세 반전" in reason for reason in reasons)


def test_survival_revalidates_reentry_relative_eligibility_and_minimum_momentum(
    monkeypatch,
):
    monkeypatch.setenv("ENABLE_CANDIDATE_ANALYSIS", "true")
    analyzer = CandidateAnalyzer(CandidateConfig.from_env(), AlertDispatcher())
    reasons = analyzer._relative_survival_rejections(
        {"relative_strength_ready": True, "is_reentry": True},
        {
            "relative_strength_ready": True,
            "relative_strength_eligible": False,
            "relative_strength_percentile": 5.0,
            "momentum_5m_pct": 0.4,
            "momentum_15m_pct": 0.6,
        },
    )

    assert any("재진입 상대강도 자격 상실" in reason for reason in reasons)
    assert any("5분 모멘텀 소멸" in reason for reason in reasons)


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
        ["완료봉 종가 위치 다소 약함(0.50)", "윗꼬리 주의(0.50)"],
        signal_id="signal-a",
    )
    dispatcher.record_candidate_rejection(
        ["완료봉 종가 위치 다소 약함(0.48)"], signal_id="signal-a"
    )
    dispatcher.record_candidate_rejection(["최근 급락 발생"], signal_id="signal-b")
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
    assert "심층검증 탈락: 고유 후보 2건 · 재검사 포함 3회" in text
    assert "주요 탈락 사유(고유 후보 기준)" in text
    assert "완료봉 종가 위치 다소 약함 1회" in text
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


def test_dispatch_checks_actual_first_target_rr_without_rounding_into_pass():
    candidate = {
        "entry_low": 99,
        "entry_high": 102,
        "chase_limit": 103,
        "stop_price": 98,
        "target_1": 104,
        "target_2": 110,
        "resistance_price": 112,
    }
    assert (
        _revalidate_candidate_for_dispatch(candidate, 100, min_risk_reward=2)[1] is None
    )
    # Still inside entry range, but price drift makes the actual first target inferior.
    assert (
        "손익비 부족"
        in _revalidate_candidate_for_dispatch(candidate, 100.001, min_risk_reward=2)[1]
    )


def test_dispatch_uses_partial_plan_and_rejects_price_drift_or_fake_runner():
    candidate = {
        "entry_low": 99, "entry_high": 102, "chase_limit": 103,
        "stop_price": 98, "target_1": 103, "target_2": 105,
        "resistance_price": 105,
        "exit_plan": {"mode": "partial_50_50", "target_1_fraction": 0.5,
                      "runner_fraction": 0.5, "runner_ceiling": 105},
    }
    refreshed, reason = _revalidate_candidate_for_dispatch(candidate, 100, min_risk_reward=2)
    assert reason is None
    assert refreshed["risk_reward"] == 2
    assert refreshed["first_target_risk_reward"] == 1.5
    assert "손익비 부족" in _revalidate_candidate_for_dispatch(candidate, 100.001, min_risk_reward=2)[1]
    candidate["target_2"] = 110
    assert "계획 무효" in _revalidate_candidate_for_dispatch(candidate, 100, min_risk_reward=2)[1]


def test_partial_plan_outcome_ends_at_runner_not_twenty_percent_observation(monkeypatch):
    monkeypatch.setenv("ENABLE_CANDIDATE_ANALYSIS", "true")
    analyzer = CandidateAnalyzer(CandidateConfig.from_env(), AlertDispatcher())
    MONITOR_STATE.trend_outcomes.clear()
    analyzer._start_trend_track({
        "market": "KRW-TEST", "source_signal": "breakout",
        "entry_reference_price": 100, "stop_price": 98, "target_1": 103,
        "target_2": 105, "target_3": 115, "target_4": 120,
        "score": 95, "target_mode": "1시간 추세보유형",
        "exit_plan": {"mode": "partial_50_50", "target_1_fraction": 0.5,
                      "runner_fraction": 0.5, "runner_ceiling": 105},
    }, 1_800_000_000)
    analyzer._observe_trend_track("KRW-TEST", 120, 1_800_000_010)
    outcome = MONITOR_STATE.trend_outcomes[0]
    assert outcome["result"] == "planned_target_2_exit"
    assert outcome["modeled_plan_return_pct"] == 4
    assert outcome["modeled_plan_r"] == 2
    assert not outcome["target_4_reached"]
    assert "KRW-TEST" not in analyzer._trend_tracks


def test_completed_recheck_survives_three_old_retries_without_fake_retest(monkeypatch):
    monkeypatch.setenv("ENABLE_CANDIDATE_ANALYSIS", "true")
    monkeypatch.setattr(MONITOR_STATE, "has_recent_signal", lambda *args: False)
    relative = {"relative_strength_ready": True, "relative_strength_eligible": False,
                "relative_strength_percentile": 2, "momentum_5m_pct": 0.4,
                "momentum_15m_pct": 2, "momentum_60m_pct": 3}
    analyzer = CandidateAnalyzer(CandidateConfig.from_env(), AlertDispatcher(), lambda *args: relative)
    analyzer._watchlist["KRW-TEST"] = {
        "created_at": 1_800_000_000, "expires_at": 1_800_010_000,
        "first_signal_price": 100, "source_signal": "breakout",
        "breakout_level": 100, "leader_recheck_count": 3,
        "last_checked_at": 1_800_000_000,
    }
    seen = []
    async def fake_analyze(alert):
        seen.append(alert)
    monkeypatch.setattr(analyzer, "_analyze", fake_analyze)
    async def run():
        assert analyzer._observe_completed_bar_watchlist("KRW-TEST", 101, 1_800_000_305)
        assert not analyzer._observe_completed_bar_watchlist("KRW-TEST", 101, 1_800_000_306)
        await asyncio.sleep(0)
        await asyncio.sleep(0)
        assert not analyzer._observe_completed_bar_watchlist("KRW-TEST", 101, 1_800_000_310)
        assert analyzer._observe_completed_bar_watchlist("KRW-TEST", 101, 1_800_000_605)
        await asyncio.sleep(0)
        await asyncio.sleep(0)
    asyncio.run(run())
    assert len(seen) == 2
    assert all(a["completed_bar_recheck"] for a in seen)
    assert all(not a.get("pullback_retest") and not a.get("is_reentry") for a in seen)
    relative["relative_strength_percentile"] = 40
    assert not analyzer._observe_completed_bar_watchlist("KRW-TEST", 101, 1_800_000_905)
    relative["relative_strength_percentile"] = 2
    analyzer._watchlist["KRW-TEST"]["leader_pullback_invalidated"] = True
    assert not analyzer._observe_completed_bar_watchlist("KRW-TEST", 101, 1_800_000_905)


def test_dispatch_rechecks_stop_width_and_hourly_capital_risk_budget():
    candidate = {
        "entry_low": 100,
        "entry_high": 102,
        "chase_limit": 103,
        "stop_price": 98,
        "target_1": 110,
        "target_2": 120,
        "holding_mode": "hourly_structure",
        "suggested_position_pct": 5,
    }
    assert (
        "손절폭 초과"
        in _revalidate_candidate_for_dispatch(candidate, 102, max_stop_loss_pct=3)[1]
    )
    refreshed, reason = _revalidate_candidate_for_dispatch(
        candidate, 101, min_risk_reward=2, max_stop_loss_pct=3
    )
    assert reason is None
    assert refreshed["suggested_position_pct"] < 5
    assert refreshed["suggested_position_pct"] / 100 * (101 - 98) / 101 * 100 <= 0.1


def test_gradual_leader_discovery_is_internal_and_deduplicated_without_minute_impulse():
    engine = SignalEngine(_config(rebreakout_enabled=False, breakout_pct=100))
    engine.relative_strength_snapshot = lambda market, now: {
        "relative_strength_ready": True,
        "relative_strength_eligible": False,
        "relative_strength_percentile": 3,
        "momentum_5m_pct": 0.4,
        "momentum_15m_pct": 1.2,
        "momentum_60m_pct": 2,
        "early_trend": False,
    }
    alerts = []
    for second in range(361):
        alerts.extend(
            engine.update(
                "KRW-IQ", 100 + second * 0.001, 1, 1_800_000_000_000 + second * 1000
            )
        )
    assert len(alerts) == 1
    assert alerts[0]["sustained_watch"] and alerts[0]["pullback_watch"]
    assert alerts[0]["internal_only"] and not alerts[0]["notify_early_watch"]
    assert alerts[0]["change_1m_pct"] < 0.1


def test_sustained_survival_can_cool_but_cannot_lose_leadership(monkeypatch):
    monkeypatch.setenv("ENABLE_CANDIDATE_ANALYSIS", "true")
    analyzer = CandidateAnalyzer(CandidateConfig.from_env(), AlertDispatcher())
    candidate = {
        "relative_strength_ready": True,
        "is_reentry": True,
        "sustained_retest": True,
        "holding_mode": "hourly_structure",
    }
    relative = {
        "relative_strength_ready": True,
        "relative_strength_eligible": False,
        "relative_strength_percentile": 3,
        "momentum_5m_pct": 0.4,
        "momentum_15m_pct": 1,
    }
    assert analyzer._relative_survival_rejections(candidate, relative) == []
    assert analyzer._relative_survival_rejections(
        candidate, {**relative, "momentum_5m_pct": -0.1}
    )
    assert analyzer._relative_survival_rejections(
        candidate, {**relative, "relative_strength_percentile": 99}
    )


def test_internal_watch_and_recheck_are_not_reported_as_rejections():
    state = monitor.MonitorState()
    for decision in [
        "preleader_watch",
        "leader_recheck_scheduled",
        "repeat_suppressed",
        "accepted",
        "rejected",
        "survival_rejected",
        "dispatch_rejected",
    ]:
        state.add_screening_record({"decision": decision, "reasons": []})
    screening = state.candidate_performance()["screening"]
    assert screening["rejected"] == 3
    assert screening["accepted"] == 1


def test_telegram_explains_sustained_path_and_completed_volume():
    candidate = _telegram_candidate()
    candidate.update(
        sustained_retest=True,
        completed_5m_volume_ratio=1.8,
        completed_15m_volume_ratio=1.2,
    )
    text = _candidate_text(candidate)
    assert "1시간 추세 재지지" in text
    assert "완료 거래량: 5분 1.80배 · 15분 1.20배" in text


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
    assert not seen[0].get("is_reentry")
    assert seen[0]["watchlist_recheck"] is True
    assert seen[0]["fresh_breakout_recheck"] is True
    assert seen[0]["origin_signal_price"] == 100.0


@pytest.mark.parametrize("cooled", [False, True])
def test_rejected_leader_is_rechecked_after_pullback_and_reclaim(monkeypatch, cooled):
    monkeypatch.setenv("ENABLE_CANDIDATE_ANALYSIS", "true")

    def relative_strength(_market, _now):
        return {
            "relative_strength_ready": True,
            "relative_strength_eligible": not cooled,
            "relative_strength_rank": 2,
            "relative_strength_universe": 100,
            "relative_strength_percentile": 2.0,
            "momentum_5m_pct": 0.4 if cooled else 2.4,
            "momentum_15m_pct": 5.0,
            "momentum_60m_pct": 8.0,
            "early_trend": not cooled,
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
    assert seen[0]["signal_id"]
    assert seen[0]["hourly_recheck"]


def test_strong_leader_pullback_is_not_blocked_by_recent_rapid_drop(monkeypatch):
    monkeypatch.setenv("ENABLE_CANDIDATE_ANALYSIS", "true")

    def relative_strength(_market, _now):
        return {
            "relative_strength_ready": True,
            "relative_strength_eligible": True,
            "relative_strength_rank": 2,
            "relative_strength_universe": 200,
            "relative_strength_percentile": 1.0,
            "momentum_5m_pct": 4.0,
            "momentum_15m_pct": 5.0,
            "early_trend": True,
        }

    analyzer = CandidateAnalyzer(
        CandidateConfig.from_env(), AlertDispatcher(), relative_strength
    )
    analyzer._watchlist["KRW-FOLD"] = {
        "market": "KRW-FOLD",
        "created_at": 1_800_000_000.0,
        "first_signal_time_utc": "2026-09-16T00:00:31+00:00",
        "first_signal_price": 63.4,
        "source_signal": "leader_volume_acceleration",
        "breakout_level": 63.4,
        "leader_peak_price": 65.0,
        "leader_pullback_seen": False,
        "leader_recheck_count": 0,
        "recheck_count": 0,
        "expires_at": 9_999_999_999.0,
    }
    monkeypatch.setattr(MONITOR_STATE, "has_recent_signal", lambda *args: True)
    seen = []

    async def fake_analyze(alert):
        seen.append(alert)

    monkeypatch.setattr(analyzer, "_analyze", fake_analyze)

    async def run():
        assert not analyzer._observe_leader_watchlist(
            "KRW-FOLD", 63.8, 1_800_000_100.0
        )
        assert analyzer._observe_leader_watchlist(
            "KRW-FOLD", 64.2, 1_800_000_110.0
        )
        await asyncio.sleep(0)

    asyncio.run(run())

    assert len(seen) == 1
    assert seen[0]["leader_pullback_recheck"] is True


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


def test_early_watch_tracks_horizons_and_missed_winners(monkeypatch):
    monkeypatch.setenv("ENABLE_CANDIDATE_ANALYSIS", "true")
    analyzer = CandidateAnalyzer(CandidateConfig.from_env(), AlertDispatcher())
    MONITOR_STATE.early_watch_events.clear()
    started = 1_800_000_000.0
    alert = {
        "time_utc": "2027-01-15T08:00:00+00:00",
        "market": "KRW-LSK",
        "signal": "leader_volume_acceleration",
        "price": 342.0,
        "relative_strength_rank": 14,
        "relative_strength_universe": 231,
        "relative_strength_percentile": 6.06,
        "momentum_3m_pct": 1.2,
        "momentum_5m_pct": 1.2,
        "preleader_volume_ratio_10m": 12.5,
        "preleader_volume_ratio_30m": 20.0,
    }

    analyzer._start_early_watch_track(alert, started)
    analyzer._observe_early_watch_track("KRW-LSK", 350.0, started + 900)
    monkeypatch.setattr("monitor.time.time", lambda: started + 1_000)
    analyzer._record_early_watch_screening(
        {**alert, "signal": "consolidation_rebreakout"},
        "rejected",
        ["호가 지지 하드차단"],
    )
    analyzer._observe_early_watch_track("KRW-LSK", 360.0, started + 1_800)
    analyzer._observe_early_watch_track("KRW-LSK", 370.0, started + 3_600)
    analyzer._observe_early_watch_track("KRW-LSK", 365.0, started + 7_200)

    performance = MONITOR_STATE.candidate_performance()[
        "early_watch_performance"
    ]
    assert performance["started_count"] == 1
    assert performance["completed_count"] == 1
    assert performance["target_first"] == 1
    assert performance["stop_first"] == 0
    assert performance["candidate_approved_count"] == 0
    assert performance["missed_target_first"] == 1
    assert performance["horizons"]["15m"]["sample_count"] == 1
    assert performance["horizons"]["30m"]["reached_5pct"] == 1
    assert performance["recent_outcomes"][0]["last_decision"] == "rejected"
    assert "호가 지지 하드차단" in performance["recent_outcomes"][0][
        "last_reasons"
    ]


def test_early_watch_records_candidate_conversion(monkeypatch):
    analyzer = CandidateAnalyzer(CandidateConfig.from_env(), AlertDispatcher())
    MONITOR_STATE.early_watch_events.clear()
    started = 1_800_000_000.0
    alert = {
        "time_utc": "2027-01-15T08:00:00+00:00",
        "market": "KRW-CVC",
        "signal": "leader_volume_acceleration",
        "price": 100.0,
    }
    analyzer._start_early_watch_track(alert, started)
    monkeypatch.setattr("monitor.time.time", lambda: started + 600)
    analyzer._record_early_watch_screening(
        {**alert, "signal": "consolidation_rebreakout"},
        "accepted",
        [],
        {"score": 94, "current_price": 102.0},
    )
    analyzer._observe_early_watch_track("KRW-CVC", 106.0, started + 7_200)

    performance = MONITOR_STATE.candidate_performance()[
        "early_watch_performance"
    ]
    assert performance["candidate_approved_count"] == 1
    assert performance["candidate_conversion_rate_pct"] == 100.0
    assert performance["recent_outcomes"][0]["candidate_approved"] is True


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


def test_candidate_message_labels_fast_leader_lane():
    candidate = _telegram_candidate()
    candidate["selection_lane"] = "fast_leader"
    candidate["survival_confirmed"] = True
    candidate["survival_seconds"] = 10

    text = _candidate_text(candidate)

    assert "[조건부 진입 후보 | 초고속 선도주 |" in text
    assert "10초 가격·거래대금·호가 유지 통과" in text


def test_preleader_schedules_fast_lane_only_for_exceptional_leader(monkeypatch):
    monkeypatch.setenv("ENABLE_CANDIDATE_ANALYSIS", "true")
    analyzer = CandidateAnalyzer(CandidateConfig.from_env(), AlertDispatcher())
    scheduled = []
    monkeypatch.setattr(
        analyzer, "schedule", lambda alert: scheduled.append(dict(alert)) or True
    )
    alert = {
        "time_utc": "2026-09-16T00:00:31+00:00",
        "market": "KRW-FOLD",
        "signal": "leader_volume_acceleration",
        "price": 63.4,
        "internal_only": True,
        "relative_strength_ready": True,
        "relative_strength_eligible": True,
        "relative_strength_rank": 3,
        "relative_strength_universe": 200,
        "relative_strength_percentile": 1.5,
        "momentum_3m_pct": 5.84,
        "momentum_5m_pct": 6.55,
        "momentum_15m_pct": 5.49,
        "early_trend": True,
        "market_regime": "neutral",
        "preleader_volume_ratio_10m": 97.19,
        "preleader_volume_ratio_30m": 144.65,
    }

    assert analyzer.watch_preleader(alert) is True

    assert len(scheduled) == 1
    assert scheduled[0]["fast_leader"] is True
    assert scheduled[0]["breakout_level"] == 63.4
    assert "internal_only" not in scheduled[0]


def test_exceptional_narrow_market_leader_can_receive_fast_check_without_watch_notice(
    monkeypatch,
):
    monkeypatch.setenv("ENABLE_CANDIDATE_ANALYSIS", "true")
    analyzer = CandidateAnalyzer(CandidateConfig.from_env(), AlertDispatcher())
    scheduled = []
    monkeypatch.setattr(
        analyzer, "schedule", lambda alert: scheduled.append(dict(alert)) or True
    )
    alert = {
        "time_utc": "2026-09-18T06:00:00+00:00",
        "market": "KRW-FOLD",
        "signal": "leader_volume_acceleration",
        "price": 63.4,
        "internal_only": True,
        "notify_early_watch": False,
        "relative_strength_ready": True,
        "relative_strength_eligible": True,
        "relative_strength_rank": 1,
        "relative_strength_universe": 200,
        "relative_strength_percentile": 0.5,
        "momentum_3m_pct": 3.0,
        "momentum_5m_pct": 4.0,
        "momentum_15m_pct": 3.0,
        "early_trend": True,
        "market_regime": "risk_off",
        "market_breadth_5m_pct": 27.0,
        "preleader_volume_ratio_10m": 11.0,
        "preleader_volume_ratio_30m": 12.0,
    }

    assert analyzer.watch_preleader(alert)
    assert scheduled[0]["fast_leader"] is True
    assert "KRW-FOLD" not in analyzer._early_watch_tracks

    # The previous 1% / 2% momentum cut discarded otherwise strong leaders.
    # This profile mirrors the observed IOTA setup: top 1.16%, +2.08% 5m,
    # and sustained turnover expansion across both preleader windows.
    recovered = dict(
        alert,
        market="KRW-IOTA",
        relative_strength_percentile=1.16,
        momentum_5m_pct=2.08,
        preleader_volume_ratio_10m=16.82,
        preleader_volume_ratio_30m=11.08,
    )
    assert analyzer._qualifies_fast_leader(recovered) is True

    weaker = dict(alert, market="KRW-SECOND", relative_strength_percentile=3.1)
    assert analyzer._qualifies_fast_leader(weaker) is False
    weak_momentum = dict(recovered, market="KRW-WEAK", momentum_5m_pct=0.99)
    assert analyzer._qualifies_fast_leader(weak_momentum) is False
    weak_turnover = dict(recovered, market="KRW-LOW-VOLUME", preleader_volume_ratio_30m=2.99)
    assert analyzer._qualifies_fast_leader(weak_turnover) is False

    # A fresh volume acceleration must still get its fast check when this
    # market is already on the 12-hour watchlist from an earlier signal.
    assert analyzer.watch_preleader(dict(alert)) is False
    assert len(scheduled) == 2


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


def test_fast_leader_runs_while_breakout_waits_for_completed_candle(monkeypatch):
    monkeypatch.setenv("ENABLE_CANDIDATE_ANALYSIS", "true")
    analyzer = CandidateAnalyzer(CandidateConfig.from_env(), AlertDispatcher())
    release = asyncio.Event()
    seen = []

    async def fake_analyze(alert):
        seen.append(dict(alert))
        await release.wait()

    monkeypatch.setattr(analyzer, "_analyze", fake_analyze)

    async def run():
        assert analyzer.schedule(
            {
                "market": "KRW-B3",
                "signal": "breakout",
                "price": 0.785,
                "time_utc": "2026-09-18T00:00:06+00:00",
            }
        )
        await asyncio.sleep(0)
        assert analyzer.watch_preleader(
            {
                "market": "KRW-B3",
                "signal": "leader_volume_acceleration",
                "price": 0.804,
                "time_utc": "2026-09-18T00:00:08+00:00",
                "relative_strength_ready": True,
                "relative_strength_eligible": True,
                "relative_strength_rank": 2,
                "relative_strength_universe": 195,
                "relative_strength_percentile": 1.03,
                "momentum_5m_pct": 3.08,
                "momentum_15m_pct": 2.94,
                "early_trend": True,
                "market_regime": "neutral",
                "preleader_volume_ratio_10m": 134.0,
                "preleader_volume_ratio_30m": 117.0,
            }
        )
        await asyncio.sleep(0)
        assert len(seen) == 2
        assert seen[0]["signal"] == "breakout"
        assert seen[1]["fast_leader"] is True
        assert analyzer._inflight_alerts["KRW-B3"]["signal"] == "breakout"
        release.set()
        await asyncio.sleep(0)
        await asyncio.sleep(0)

    asyncio.run(run())
    assert not analyzer._fast_inflight_markets


def test_parallel_checks_only_dispatch_one_candidate_per_market(monkeypatch):
    monkeypatch.setenv("ENABLE_CANDIDATE_ANALYSIS", "true")
    analyzer = CandidateAnalyzer(CandidateConfig.from_env(), AlertDispatcher())
    sent = []

    async def fake_send(candidate):
        sent.append(candidate)
        await asyncio.sleep(0)
        return True

    monkeypatch.setattr(analyzer.dispatcher, "send_candidate", fake_send)

    async def run():
        results = await asyncio.gather(
            analyzer._send_candidate_once({"market": "KRW-B3", "score": 91}),
            analyzer._send_candidate_once({"market": "KRW-B3", "score": 90}),
        )
        assert results == [(True, False), (False, True)]

    asyncio.run(run())
    assert len(sent) == 1


def test_upgraded_signal_waits_for_eligible_completed_candle(monkeypatch):
    analyzer = CandidateAnalyzer(CandidateConfig.from_env(), AlertDispatcher())
    now = datetime(2026, 9, 18, 0, 11, 52, tzinfo=timezone.utc).timestamp()
    waits = []
    snapshots = []
    alert = {
        "market": "KRW-ICX",
        "signal": "price_volume_surge",
        "price": 18.4,
        "confirmation_started_at_utc": "2026-09-18T00:11:26+00:00",
    }

    def fake_time():
        return now

    async def fake_sleep(seconds):
        nonlocal now
        waits.append(seconds)
        now += seconds
        if len(waits) == 1:
            alert["signal"] = "breakout"
            alert["confirmation_started_at_utc"] = "2026-09-18T00:11:42+00:00"

    async def fake_snapshot(_market):
        snapshots.append(now)
        return {"ticker": {"trade_price": 19.0}}

    monkeypatch.setattr(monitor.time, "time", fake_time)
    monkeypatch.setattr(monitor.asyncio, "sleep", fake_sleep)
    monkeypatch.setattr(analyzer, "_market_snapshot", fake_snapshot)
    monkeypatch.setattr(analyzer, "_evaluate_snapshot", lambda _a, _s: (None, ["검증 실패"]))
    monkeypatch.setattr(analyzer, "_remember_rejected", lambda *_args: None)
    asyncio.run(analyzer._analyze(alert))

    assert waits == [30, 40.0]
    assert snapshots == [datetime(2026, 9, 18, 0, 13, 2, tzinfo=timezone.utc).timestamp()]


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

    assert "1차 전량 청산 가정 모의 평균: +0.20%" in text
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
    assert not seen[0].get("is_reentry")
    assert seen[0]["fresh_breakout_recheck"] is True
    assert seen[0]["origin_signal_price"] == 342.0
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


def _qualified_partial_telegram_candidate():
    candidate = _telegram_candidate()
    candidate.update({
        "stop_price": 98.05, "entry_high": 100.1, "target_1": 103, "target_2": 104.9,
        "target_2_pct": 4.9, "risk_reward": 2.03, "first_target_risk_reward": 1.5385,
        "exit_plan": {"mode": "partial_50_50", "target_1_fraction": 0.5,
                      "runner_fraction": 0.5, "runner_ceiling": 104.9},
        "higher_timeframe_context": {"ready": True, "hourly_established": True,
            "fifteen_intact": True, "fifteen": {"above_ma20": True},
            "hourly": {"status": "상승"}, "four_hour": {"status": "상승"}},
        "double_bb_confirmed": True, "first_retest_confirmed": True,
        "completed_5m_volume_ratio": 2, "completed_15m_volume_ratio": 2,
    })
    return candidate


def test_qualified_partial_plan_reaches_mock_telegram_below_generic_target_floor(monkeypatch):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "test-token")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "test-chat")
    monkeypatch.setenv("TELEGRAM_CANDIDATE_MIN_TARGET_2_PCT", "5")
    monkeypatch.setenv("CANDIDATE_MIN_RISK_REWARD", "2")
    dispatcher = AlertDispatcher()
    sent = []
    class Response:
        def raise_for_status(self):
            pass
    class Client:
        def __init__(self, *args, **kwargs):
            pass
        async def __aenter__(self):
            return self
        async def __aexit__(self, *args):
            pass
        async def post(self, url, json):
            sent.append(json)
            return Response()
    monkeypatch.setattr("monitor.httpx.AsyncClient", Client)
    candidate = _qualified_partial_telegram_candidate()
    assert asyncio.run(dispatcher.send_candidate(candidate))
    assert "청산 계획: 1차 50%" in sent[0]["text"]
    assert "delivery_suppression_reasons" not in candidate
    ordinary = dict(candidate)
    ordinary.pop("exit_plan")
    assert not asyncio.run(dispatcher.send_candidate(ordinary))
    assert "일반형 2차 목표 미달" in ordinary["delivery_suppression_reasons"][0]
    assert len(sent) == 1


@pytest.mark.parametrize("cause", ["risk", "fake_runner", "stop_width", "unconfirmed", "score", "volume"])
def test_partial_delivery_does_not_bypass_final_qualification(monkeypatch, cause):
    monkeypatch.setenv("CANDIDATE_MIN_RISK_REWARD", "2")
    dispatcher = AlertDispatcher()
    candidate = _qualified_partial_telegram_candidate()
    if cause == "risk":
        candidate["current_price"] = 100.017  # 1.9995R must not round into a 2R pass
    elif cause == "fake_runner":
        candidate["target_2"] = 110
    elif cause == "stop_width":
        candidate["stop_price"] = 96
    elif cause == "unconfirmed":
        candidate["first_retest_confirmed"] = False
    elif cause == "score":
        candidate["score"] = 89
    elif cause == "volume":
        candidate["completed_5m_volume_ratio"] = 1.49
    assert dispatcher.candidate_delivery_reasons(candidate)


def test_daily_report_separates_partial_first_touch_from_completed_plan():
    state = monitor.MonitorState()
    state.candidate_outcomes.append({
        "market": "KRW-W", "result": "target_1_first", "entry_price": 100,
        "target_1": 103, "exit_price": 104, "exit_plan": {"mode": "partial_50_50"},
        "completed_at_utc": "2026-10-01T01:00:00+00:00",
    })
    before = state.daily_performance("2026-10-01")
    assert before["target_1_first"] == 1
    assert before["simulated_return_average_pct"] is None
    assert before["partial_exit_plan_performance"]["sample_count"] == 0
    state.trend_outcomes.append({
        "exit_plan": {"mode": "partial_50_50"}, "entry_price": 100,
        "stop_price": 98, "modeled_plan_return_pct": 1.5,
        "completed_at_utc": "2026-10-01T02:00:00+00:00",
    })
    after = state.daily_performance("2026-10-01", 0.2)
    assert after["partial_exit_plan_performance"]["mean_net_modeled_return_pct"] == 1.3
    assert "비용 차감 모의 평균 +1.30%" in _daily_performance_text(after)


def test_daily_first_target_baseline_caps_upward_gap_at_published_target():
    state = monitor.MonitorState()
    state.candidate_outcomes.append({
        "result": "target_1_first", "entry_price": 100,
        "target_1": 103, "exit_price": 110,
        "completed_at_utc": "2026-10-01T01:00:00+00:00",
    })
    assert state.daily_performance("2026-10-01", 0.2)["simulated_return_average_pct"] == 2.8


def test_existing_quiet_watch_does_not_suppress_first_intraday_notice(monkeypatch):
    monkeypatch.setenv("ENABLE_CANDIDATE_ANALYSIS", "true")
    analyzer = CandidateAnalyzer(CandidateConfig.from_env(), AlertDispatcher())
    monkeypatch.setattr(analyzer, "schedule", lambda alert: False)
    quiet = {"market": "KRW-ICX", "signal": "breakout", "price": 20.8,
             "breakout_level": 20.5, "pullback_watch": True, "notify_early_watch": False}
    assert analyzer.watch_preleader(quiet)
    notice = dict(quiet, signal="leader_volume_acceleration", price=21.5,
                  breakout_level=21.4, pullback_watch=False, notify_early_watch=True)
    assert not analyzer.watch_preleader(notice)
    assert analyzer.early_watch_due(notice)
    assert analyzer._watchlist["KRW-ICX"]["breakout_level"] == 20.5
    # Failed/suppressed transport remains eligible; only success deduplicates.
    assert analyzer.early_watch_due(notice)
    analyzer.mark_early_watch_delivered(notice)
    assert not analyzer.early_watch_due(notice)
    analyzer._remember_rejected(dict(notice, watchlist_recheck=True), ["RSI"], 22)
    assert analyzer._watchlist["KRW-ICX"]["breakout_level"] == 20.5
    assert not analyzer.early_watch_due(notice)


def test_explosive_lane_is_visible_in_telegram():
    candidate = dict(_telegram_candidate(), selection_lane="explosive_leader")
    assert "완료 5분 폭발적 선도주" in _candidate_text(candidate)


def test_explosive_survival_requires_leadership_to_remain_top_two_percent(monkeypatch):
    monkeypatch.setenv("ENABLE_CANDIDATE_ANALYSIS", "true")
    analyzer = CandidateAnalyzer(CandidateConfig.from_env(), AlertDispatcher())
    candidate = {"selection_lane": "explosive_leader", "relative_strength_ready": True}
    relative = {"relative_strength_ready": True, "relative_strength_eligible": True,
                "relative_strength_percentile": 3, "momentum_5m_pct": 2, "momentum_15m_pct": 3}
    assert any("상위 2%" in reason for reason in analyzer._relative_survival_rejections(candidate, relative))
    relative["relative_strength_percentile"] = 1
    assert analyzer._relative_survival_rejections(candidate, relative) == []


def test_new_completed_bar_is_rechecked_even_after_late_previous_bar_rejection(monkeypatch):
    monkeypatch.setenv("ENABLE_CANDIDATE_ANALYSIS", "true")
    monkeypatch.setattr(MONITOR_STATE, "has_recent_signal", lambda *args: False)
    relative = {"relative_strength_ready": True, "relative_strength_eligible": False,
                "relative_strength_percentile": 1, "momentum_5m_pct": .3,
                "momentum_15m_pct": .9, "momentum_60m_pct": 0}
    analyzer = CandidateAnalyzer(CandidateConfig.from_env(), AlertDispatcher(), lambda *args: relative)
    analyzer._watchlist["KRW-TEST"] = {"created_at": 1_800_000_000,
        "expires_at": 1_800_010_000, "source_signal": "breakout", "breakout_level": 100,
        "first_signal_price": 100, "last_checked_at": 1_800_000_295}
    analyzer._last_leader_recheck_at["KRW-TEST"] = 1_800_000_295
    seen = []
    async def fake_analyze(alert): seen.append(alert)
    monkeypatch.setattr(analyzer, "_analyze", fake_analyze)
    async def run():
        assert not analyzer._observe_completed_bar_watchlist("KRW-TEST", 101, 1_800_000_304)
        assert analyzer._observe_completed_bar_watchlist("KRW-TEST", 101, 1_800_000_305)
        await asyncio.sleep(0)
        await asyncio.sleep(0)
        assert not analyzer._observe_completed_bar_watchlist("KRW-TEST", 101, 1_800_000_310)
    asyncio.run(run())
    assert len(seen) == 1 and seen[0]["breakout_level"] == 100


def _explosive_telegram_candidate():
    candidate = _telegram_candidate()
    candidate.update(score=80, selection_lane="explosive_leader", stop_price=99,
                     suggested_position_pct=5, relative_strength_percentile=0.5,
                     survival_confirmed=True, survival_seconds=60,
                     survival_btc_5m_pct=0, survival_btc_15m_pct=0, survival_btc_live_pct=0,
                     explosive_context={"confirmed": True, "retest": False, "volume_5m": 10,
                                        "volume_15m": 4, "close_position": 0.9, "upper_wick": 0.1})
    return candidate


def test_verified_explosive_candidate_reaches_mock_telegram_with_separate_score_floor(monkeypatch):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "test-token")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "test-chat")
    monkeypatch.setenv("TELEGRAM_CANDIDATE_MIN_SCORE", "90")
    dispatcher = AlertDispatcher()
    sent = []
    class Response:
        def raise_for_status(self): pass
    class Client:
        def __init__(self, *args, **kwargs): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *args): pass
        async def post(self, url, json):
            sent.append(json["text"])
            return Response()
    monkeypatch.setattr("monitor.httpx.AsyncClient", Client)
    candidate = _explosive_telegram_candidate()
    assert dispatcher.candidate_delivery_reasons(candidate) == []
    assert asyncio.run(dispatcher.send_candidate(candidate))
    assert len(sent) == 1 and "완료 5분 폭발적 선도주" in sent[0]
    ordinary = dict(candidate, selection_lane="standard")
    assert any("점수 미달" in reason for reason in dispatcher.candidate_delivery_reasons(ordinary))


@pytest.mark.parametrize("cause", ["survival", "btc", "risk", "volume", "shape", "score", "disabled"])
def test_telegram_explosive_score_exception_requires_actual_qualified_plan(monkeypatch, cause):
    candidate = _explosive_telegram_candidate()
    if cause == "survival": candidate["survival_confirmed"] = False
    elif cause == "btc": candidate["survival_btc_5m_pct"] = -1
    elif cause == "risk": candidate["stop_price"] = 97
    elif cause == "volume": candidate["explosive_context"]["volume_15m"] = 0.5
    elif cause == "shape": candidate["explosive_context"]["upper_wick"] = 0.8
    elif cause == "score": candidate["score"] = 79
    elif cause == "disabled": monkeypatch.setenv("CANDIDATE_EXPLOSIVE_LEADER_ENABLED", "false")
    assert AlertDispatcher().candidate_delivery_reasons(candidate)


@pytest.mark.parametrize("path", ["fast", "completed", "pullback"])
@pytest.mark.parametrize("lane", ["explosive", "rsi_exception", "completed_breakout", "trade_flow", "wb_retest"])
def test_explosive_runtime_never_skips_full_survival_for_other_signal_paths(monkeypatch, path, lane):
    monkeypatch.setenv("ENABLE_CANDIDATE_ANALYSIS", "true")
    monkeypatch.setattr("monitor.time.time", lambda: 1_800_000_010)
    analyzer = CandidateAnalyzer(CandidateConfig.from_env(), AlertDispatcher())
    alert = {"market": "KRW-TEST", "signal": "breakout", "price": 100}
    if path == "fast": alert["fast_leader"] = True
    elif path == "completed": alert.update(completed_bar_recheck=True, original_signal_time_utc="2026-10-01T00:00:00+00:00")
    else: alert["leader_pullback_recheck"] = True
    waits, snapshots = [], []
    async def sleep(seconds): waits.append(seconds)
    async def snapshot(market):
        snapshots.append(market)
        if len(snapshots) > 1: raise RuntimeError("end mock at survival snapshot")
        return {"ticker": {"trade_price": 100}}
    monkeypatch.setattr("monitor.asyncio.sleep", sleep)
    monkeypatch.setattr(analyzer, "_market_snapshot", snapshot)
    candidate = _explosive_telegram_candidate()
    if lane == "rsi_exception":
        candidate.update(selection_lane="standard", rsi_breakout_exception=True)
    elif lane == "completed_breakout":
        candidate.update(selection_lane="standard", completed_breakout_entry=True)
    elif lane == "trade_flow":
        candidate.update(selection_lane="trade_flow_leader", trade_flow_leader=True)
    elif lane == "wb_retest":
        candidate.update(selection_lane="standard", completed_wb_retest_entry=True)
    monkeypatch.setattr(analyzer, "_evaluate_snapshot", lambda *args: (candidate, []))
    asyncio.run(analyzer._analyze(alert))
    assert len(snapshots) == 2
    assert waits[-1] == 60
    if path == "completed": assert waits == [60]


def test_dispatch_does_not_send_when_reclassified_resistance_support_is_lost():
    candidate = _telegram_candidate()
    price = float(candidate["entry_high"])
    candidate["cleared_resistance_levels"] = [price * 1.003]
    refreshed, rejection = _revalidate_candidate_for_dispatch(candidate, price)
    assert refreshed is None
    assert "지지 전환 실패" in rejection


def test_wb_retest_notification_identifies_completed_five_minute_evidence():
    candidate = _telegram_candidate()
    candidate.update(completed_wb_retest_entry=True, double_bb_enabled=True,
                     double_bb_status='WB 동시 돌파 후 첫 눌림 재지지', double_bb_timeframe='5분')
    text = monitor._candidate_text(candidate)
    assert '5분WB 첫 재지지' in text
    assert '5분 WB 판정: WB 동시 돌파 후 첫 눌림 재지지' in text


def test_wb_retest_survival_rejects_lost_relative_eligibility():
    analyzer = CandidateAnalyzer(CandidateConfig.from_env(), AlertDispatcher())
    candidate = {'relative_strength_ready': True, 'completed_wb_retest_entry': True}
    relative = {'relative_strength_ready': True, 'relative_strength_percentile': 1,
                'relative_strength_eligible': False, 'momentum_5m_pct': 2, 'momentum_15m_pct': 2}
    reasons = analyzer._relative_survival_rejections(candidate, relative)
    assert '생존 중 5분 WB 첫 재지지 상대강도 자격 상실' in reasons


def test_resolve_all_krw_markets_requests_uncached_listing(monkeypatch):
    calls = []
    class Client:
        async def markets(self, **kwargs):
            calls.append(kwargs)
            return [{'market': 'KRW-POD'}, {'market': 'BTC-ETH'}, {'market': 'KRW-BTC'}]
        async def close(self): pass
    monkeypatch.setattr(monitor, 'UpbitPublicClient', Client)
    from types import SimpleNamespace
    result = asyncio.run(monitor._resolve_markets(SimpleNamespace(all_krw_markets=True)))
    assert result == ['KRW-BTC', 'KRW-POD']
    assert calls == [{'cache_seconds': 0}]


def test_screening_snapshot_exposes_queue_and_fetch_delay():
    now = datetime(2026, 10, 2, 8, 50, 5, tzinfo=timezone.utc)
    alert = {}
    snapshot = {'as_of': now, 'requested_at': now - timedelta(seconds=12),
                'fetched_at': now + timedelta(seconds=4), 'ticker': {'trade_price': 101}}
    monitor.CandidateAnalyzer._record_screening_snapshot(alert, snapshot, 'entry_screen')
    assert alert['screening_snapshot']['queue_wait_seconds'] == 12
    assert alert['screening_snapshot']['fetch_duration_seconds'] == 4


def test_qualified_surge_reaches_validation_without_waiting_for_a_pullback(monkeypatch):
    monkeypatch.setenv('ENABLE_CANDIDATE_ANALYSIS', 'true')
    analyzer = CandidateAnalyzer(CandidateConfig.from_env(), AlertDispatcher())
    seen = []
    async def analyze(alert): seen.append(alert)
    monkeypatch.setattr(analyzer, '_analyze', analyze)
    alert = {'market': 'KRW-TEST', 'signal': 'price_volume_surge', 'price': 100,
             'relative_strength_ready': True, 'relative_strength_eligible': True,
             'relative_strength_percentile': 1, 'momentum_5m_pct': 2,
             'momentum_15m_pct': 3}
    async def run():
        assert analyzer.schedule(alert)
        await asyncio.sleep(0)
    asyncio.run(run())
    assert len(seen) == 1
    assert not seen[0].get('is_reentry')
    assert not seen[0].get('pullback_retest')
    assert not seen[0].get('leader_pullback_recheck')


def test_scanner_rebreakout_in_an_active_watch_is_a_fresh_setup(monkeypatch):
    monkeypatch.setenv('ENABLE_CANDIDATE_ANALYSIS', 'true')
    now = 1_800_000_000.0
    monkeypatch.setattr(monitor.time, 'time', lambda: now)
    monkeypatch.setattr(MONITOR_STATE, 'has_recent_signal', lambda *args: False)
    analyzer = CandidateAnalyzer(CandidateConfig.from_env(), AlertDispatcher())
    analyzer._watchlist['KRW-ORCA'] = {
        'market': 'KRW-ORCA', 'created_at': now - 600, 'expires_at': now + 10_000,
        'first_signal_time_utc': '2026-10-05T13:10:36+00:00',
        'first_signal_price': 2705, 'source_signal': 'leader_volume_acceleration',
        'breakout_level': 2705,
    }
    analyzer._signal_tracks['KRW-ORCA'] = {
        'signal_id': 'old-origin', 'expires_at': now + 10_000,
        'market': 'KRW-ORCA', 'signal_price': 2705,
    }
    seen = []
    async def analyze(alert): seen.append(dict(alert))
    monkeypatch.setattr(analyzer, '_analyze', analyze)
    alert_time = '2026-10-05T18:22:02+00:00'
    alert = {'time_utc': alert_time, 'market': 'KRW-ORCA',
        'signal': 'consolidation_rebreakout', 'price': 2818,
        'relative_strength_ready': True, 'relative_strength_eligible': True,
        'relative_strength_percentile': .8, 'momentum_5m_pct': 2,
        'momentum_15m_pct': 2.5}

    async def run():
        assert analyzer.schedule(alert)
        await asyncio.sleep(0)
        await asyncio.sleep(0)
    asyncio.run(run())

    assert len(seen) == 1
    fresh = seen[0]
    assert fresh['fresh_breakout_recheck'] and fresh['watchlist_recheck']
    assert not fresh.get('is_reentry')
    assert fresh['original_signal_time_utc'] == alert_time
    assert fresh['origin_signal_price'] == 2705
    assert fresh['parent_signal_id'] == 'old-origin'
    assert fresh['signal_id'] != 'old-origin'


def test_completed_breakout_message_reports_actual_wb_timeframe():
    candidate = _telegram_candidate()
    candidate.update(completed_breakout_entry=True, double_bb_enabled=True,
                     double_bb_timeframe='5분', double_bb_status='WB 두 상단·직전 매물대 동시 돌파')
    message = monitor._candidate_text(candidate)
    assert '진입형: 눌림 없는 완료봉 돌파' in message
    assert '5분 WB 판정:' in message


def test_no_pullback_route_revalidates_leader_rank_and_eligibility():
    analyzer = CandidateAnalyzer(CandidateConfig.from_env(), AlertDispatcher())
    candidate = {'relative_strength_ready': True, 'completed_breakout_entry': True}
    relative = {'relative_strength_ready': True, 'relative_strength_eligible': True,
                'relative_strength_percentile': 1, 'momentum_5m_pct': 1.2,
                'momentum_15m_pct': 2}
    assert analyzer._relative_survival_rejections(candidate, relative) == []
    relative.update(relative_strength_percentile=6, relative_strength_eligible=False)
    reasons = analyzer._relative_survival_rejections(candidate, relative)
    assert len(reasons) == 2


def _flow_telegram_candidate(now):
    from test_trade_flow import strong_flow
    candidate = _explosive_telegram_candidate()
    candidate.update(score=86, selection_lane='trade_flow_leader', trade_flow_leader=True,
                     survival_trade_flow=strong_flow(now, float(candidate['current_price'])),
                     first_target_risk_reward=3, double_bb_enabled=True, double_bb_confirmed=True,
                     double_bb_timeframe='5분', double_bb_status='완료 돌파',
                     completed_5m_volume_ratio=2, completed_15m_volume_ratio=1.5)
    return candidate


def test_new_trade_flow_is_exposed_by_live_engine_not_ohlc_warmup():
    engine = SignalEngine(_config())
    now = 1800000100
    for index in range(61):
        engine.update('KRW-TEST', 100 + index * .004, 2000, (now - 60 + index) * 1000,
                      ask_bid='ASK' if index % 3 == 0 else 'BID', sequential_id=index)
    context = engine.relative_strength_snapshot('KRW-TEST', now)['trade_flow_context']
    assert context['ready'] and context['trade_count'] == 60
    assert context['buy_share_pct'] > 60
    engine.trade_flow.clear()
    assert not engine.relative_strength_snapshot('KRW-TEST', now)['trade_flow_context']['ready']


def test_telegram_flow_route_preserves_deductions_and_uses_current_execution_evidence(monkeypatch):
    now = 1800000100
    monkeypatch.setattr('monitor.time.time', lambda: now)
    monkeypatch.setenv('TELEGRAM_CANDIDATE_MIN_SCORE', '90')
    candidate = _flow_telegram_candidate(now)
    dispatcher = AlertDispatcher()
    assert dispatcher.candidate_delivery_reasons(candidate) == []
    text = monitor._candidate_text(candidate)
    assert '체결 매수 지속형' in text and '실제 체결:' in text and '60초' in text
    candidate['trade_flow_leader'] = False
    assert dispatcher.candidate_delivery_reasons(candidate)


@pytest.mark.parametrize('cause', ['stale', 'selling', 'survival', 'btc', 'risk', 'score', 'rr', 'disabled'])
def test_telegram_flow_route_requires_all_fresh_risk_qualifications(monkeypatch, cause):
    now = 1800000100
    monkeypatch.setattr('monitor.time.time', lambda: now)
    candidate = _flow_telegram_candidate(now)
    if cause == 'stale': candidate['survival_trade_flow']['as_of'] -= 11
    elif cause == 'selling':
        for w in candidate['survival_trade_flow']['windows']: w['buy_krw'] = 0
    elif cause == 'survival': candidate['survival_confirmed'] = False
    elif cause == 'btc': candidate['survival_btc_5m_pct'] = -1
    elif cause == 'risk': candidate['stop_price'] = 95
    elif cause == 'score': candidate['score'] = 85
    elif cause == 'rr': candidate['first_target_risk_reward'] = 1.99
    elif cause == 'disabled': monkeypatch.setenv('CANDIDATE_TRADE_FLOW_LEADER_ENABLED', 'false')
    assert AlertDispatcher().candidate_delivery_reasons(candidate)


def test_flow_survival_does_not_cool_out_an_intact_leader_but_still_requires_rank_and_breadth():
    analyzer = CandidateAnalyzer(CandidateConfig.from_env(), AlertDispatcher())
    candidate = {'trade_flow_leader': True, 'relative_strength_ready': True, 'watchlist_recheck': True}
    relative = {'relative_strength_ready': True, 'relative_strength_eligible': False,
        'relative_strength_percentile': 1, 'momentum_5m_pct': .6, 'momentum_15m_pct': 2,
        'momentum_60m_pct': 3, 'market_breadth_5m_pct': 30}
    assert analyzer._relative_survival_rejections(candidate, relative) == []
    for key, value in [('relative_strength_percentile', 3), ('market_breadth_5m_pct', 19),
                       ('momentum_5m_pct', -.1), ('momentum_60m_pct', 0)]:
        assert analyzer._relative_survival_rejections(candidate, {**relative, key: value})


def test_fresh_structure_survival_rechecks_selected_leadership_without_sixty_minute_gate():
    analyzer = CandidateAnalyzer(CandidateConfig.from_env(), AlertDispatcher())
    candidate = {"completed_structure_entry": True, "fresh_breakout_recheck": True,
                 "structure_confirmation_basis": "relative_leadership",
                 "relative_strength_ready": True}
    relative = {"relative_strength_ready": True, "relative_strength_percentile": 1.2,
                "momentum_5m_pct": .3, "momentum_15m_pct": .9,
                "momentum_60m_pct": 0, "market_breadth_5m_pct": 27}
    assert analyzer._relative_survival_rejections(candidate, relative) == []
    assert analyzer._relative_survival_rejections(
        candidate, {**relative, "momentum_15m_pct": .1})
