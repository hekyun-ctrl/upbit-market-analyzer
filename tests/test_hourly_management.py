import asyncio
import time

from candidate_analysis import CandidateConfig
from monitor import AlertDispatcher, CandidateAnalyzer, MONITOR_STATE, _management_text


def context(*, established=True, close=108, support=106, volume=0.2):
    return {
        "ready": True,
        "hourly_established": established,
        "fifteen_intact": True,
        "hourly": {
            "status": "상승 유지" if established else "눌림·혼조",
            "last_volume_ratio": volume,
        },
        "fifteen": {"close": close, "support": support},
        "four_hour": {"status": "하락 우위"},
    }


def setup(monkeypatch, *, mode="initial_signal"):
    monkeypatch.setenv("CANDIDATE_HIGHER_TIMEFRAME_ENABLED", "true")
    analyzer = CandidateAnalyzer(CandidateConfig.from_env(), AlertDispatcher())
    events = []
    monkeypatch.setattr(
        analyzer,
        "_queue_management_update",
        lambda market, state, event, price: events.append((event, price)),
    )
    candidate = {
        "market": "KRW-TEST",
        "source_signal": "breakout",
        "entry_reference_price": 100,
        "stop_price": 97,
        "target_1": 103,
        "target_2": 108,
        "score": 95,
        "target_mode": (
            "균형 위험비형" if mode == "initial_signal" else "1시간 추세보유형"
        ),
        "holding_mode": mode,
        "higher_timeframe_context": context(),
        "price_tick": 0.1,
    }
    if mode == "hourly_structure":
        candidate.update(target_2=110, target_3=115, target_4=120)
    analyzer._start_trend_track(candidate, 1000)
    return analyzer, events, candidate


def test_initial_track_can_promote_without_waiting_for_four_hour_ma(monkeypatch):
    analyzer, events, _ = setup(monkeypatch)
    state = analyzer._trend_tracks["KRW-TEST"]
    assert state["expires_at"] == 1000 + 86400
    analyzer._observe_trend_track("KRW-TEST", 104, 1100)
    analyzer._apply_holding_context(
        "KRW-TEST", context(close=104, support=102), 104, 1101
    )
    assert state["holding_mode"] == "hourly_structure"
    assert state["target_2"] == 110 and state["target_4"] == 120
    assert state["stop_price"] == 97
    assert any(event == "hourly_promoted" for event, _ in events)


def test_initial_second_target_does_not_end_later_trend_observation(monkeypatch):
    analyzer, _, _ = setup(monkeypatch)
    analyzer._observe_trend_track("KRW-TEST", 108, 1100)
    assert "KRW-TEST" in analyzer._trend_tracks
    analyzer._apply_holding_context("KRW-TEST", context(), 108, 1101)
    state = analyzer._trend_tracks["KRW-TEST"]
    assert state["holding_mode"] == "hourly_structure"
    assert state["target_2"] == 110
    assert not state["target_2_reached"]


def test_short_term_cooling_and_hourly_warning_do_not_force_full_exit(monkeypatch):
    analyzer, events, _ = setup(monkeypatch, mode="hourly_structure")
    analyzer._observe_trend_track("KRW-TEST", 108, 1100)
    analyzer._apply_holding_context("KRW-TEST", context(volume=0.1), 108, 1101)
    analyzer._apply_holding_context("KRW-TEST", context(established=False), 107, 1102)
    analyzer._apply_holding_context("KRW-TEST", context(established=False), 107, 1103)
    assert "KRW-TEST" in analyzer._trend_tracks
    assert sum(event == "hourly_caution" for event, _ in events) == 1


def test_structure_protection_never_lowers_and_requires_completed_close(monkeypatch):
    analyzer, events, _ = setup(monkeypatch, mode="hourly_structure")
    analyzer._observe_trend_track("KRW-TEST", 108, 1100)
    analyzer._apply_holding_context("KRW-TEST", context(), 108, 1101)
    state = analyzer._trend_tracks["KRW-TEST"]
    level = state["structure_protection_price"]
    assert 100 < level < 106
    analyzer._apply_holding_context("KRW-TEST", context(support=102), 108, 1102)
    assert state["structure_protection_price"] == level
    analyzer._observe_trend_track("KRW-TEST", 104, 1103)
    assert "KRW-TEST" in analyzer._trend_tracks  # An intrabar wick isn't a close.
    analyzer._apply_holding_context(
        "KRW-TEST", context(close=104, support=102), 104, 1104
    )
    assert "KRW-TEST" not in analyzer._trend_tracks
    assert events[-1][0] == "structure_exit"
    report = MONITOR_STATE.candidate_performance()["trend_tracking_performance"]
    assert report["recent"][0]["result"] == "structure_exit"


def test_original_stop_and_breakeven_are_immediate_even_in_hourly_mode(monkeypatch):
    analyzer, events, candidate = setup(monkeypatch, mode="hourly_structure")
    analyzer._observe_trend_track("KRW-TEST", 96, 1100)
    assert "KRW-TEST" not in analyzer._trend_tracks
    assert events[-1][0] == "stop"
    analyzer._start_trend_track(candidate, 1200)
    analyzer._observe_trend_track("KRW-TEST", 104, 1300)
    analyzer._observe_trend_track("KRW-TEST", 100, 1301)
    assert "KRW-TEST" not in analyzer._trend_tracks
    assert events[-1][0] == "protected"


def test_unavailable_context_and_losing_trade_cannot_widen_stop(monkeypatch):
    analyzer, events, _ = setup(monkeypatch)
    state = analyzer._trend_tracks["KRW-TEST"]
    analyzer._apply_holding_context("KRW-TEST", {"ready": False}, 102, 1100)
    analyzer._apply_holding_context("KRW-TEST", context(), 99, 1101)
    assert state["holding_mode"] == "initial_signal"
    assert state["stop_price"] == 97
    assert not events


def test_refresh_is_bounded_and_does_not_mutate_replacement_track(monkeypatch):
    analyzer, _, candidate = setup(monkeypatch)
    calls = []

    async def run():
        gate = asyncio.Event()

        async def fake_snapshot(market):
            calls.append(market)
            await gate.wait()
            return {
                "ticker": {"trade_price": 104},
                "candles_15m": [],
                "candles_60m": [],
                "candles_240m": [],
            }

        monkeypatch.setattr(analyzer, "_holding_snapshot", fake_snapshot)
        monkeypatch.setattr("monitor.higher_timeframe_context", lambda *args: context())
        now = time.time()
        analyzer._start_trend_track(candidate, now)
        for _ in range(10):
            analyzer._schedule_holding_refresh("KRW-TEST", now)
        await asyncio.sleep(0)
        assert calls == ["KRW-TEST"]
        analyzer._start_trend_track(candidate, now + 1)
        replacement = analyzer._trend_tracks["KRW-TEST"]
        gate.set()
        await asyncio.gather(*list(analyzer._tasks))
        assert replacement["holding_mode"] == "initial_signal"
        assert not analyzer._trend_refresh_inflight

    asyncio.run(run())


def test_failed_optional_higher_fetch_keeps_initial_screening_data(monkeypatch):
    analyzer, _, _ = setup(monkeypatch)

    class Client:
        async def ticker(self, market):
            return {"trade_price": 100}

        async def candles(self, market, interval, count):
            if interval in ("minute60", "minute240"):
                raise RuntimeError("temporary rate limit")
            return [{"trade_price": 100}]

        async def close(self):
            pass

    async def books(client, market):
        return [{}]

    monkeypatch.setattr("monitor.UpbitPublicClient", Client)
    monkeypatch.setattr(analyzer, "_orderbook_samples", books)
    snapshot = asyncio.run(analyzer._market_snapshot("KRW-TEST"))
    assert snapshot["candles_60m"] == [] and snapshot["candles_240m"] == []
    assert snapshot["candles_5m"] == [{"trade_price": 100}]


def test_hourly_management_message_explains_observation_and_protection():
    text = _management_text(
        {
            "event": "hourly_promoted",
            "market": "KRW-TEST",
            "entry_price": 100,
            "current_price": 104,
            "target_2": 110,
            "target_3": 115,
            "target_4": 120,
            "structure_protection_price": 101.5,
        }
    )
    assert "1시간 추세보유 전환" in text
    assert "15분 종가 보호선" in text
    assert "실제 체결 여부를 알 수 없는" in text
