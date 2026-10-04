"""Independent, timestamped public-bar regressions; subsequent returns are not inputs."""
import copy
import json
from datetime import datetime
from pathlib import Path

import pytest
import candidate_analysis as ca
import monitor
from test_candidate_analysis import _config, _evaluate_flow
from test_completed_structure_entry import structure_fixture, delivery_candidate


def case(market):
    data = json.loads((Path(__file__).parent / "fixtures/structure-route-20261005.json").read_text())
    row = data["cases"][market]
    return datetime.fromisoformat(row["record"]["screening_snapshot"]["as_of_utc"]), row["bars"]


@pytest.mark.parametrize("market,confirmed,mode", [
    ("KRW-SUI", True, "price_breakout"),
    ("KRW-AKT", True, "first_retest"),
    ("KRW-BEAM", False, None),
    ("KRW-POD", False, None),
    ("KRW-XLM", False, None),
])
def test_real_completed_price_structure_and_valid_rejections(market, confirmed, mode):
    now, bars = case(market)
    proof = ca._completed_structure_context(bars["minute1"], bars["minute5"], bars["minute15"], now, _config())
    assert proof["confirmed"] is confirmed
    if mode:
        assert proof["structure_mode"] == mode
    else:
        assert proof["blockers"]
        assert "미충족" in proof["status"]
    if market == "KRW-SUI":
        assert not proof["wb_confirmed"]
        assert proof["level"] == 1693 and proof["close"] == 1702
        assert proof["volume_5m"] == pytest.approx(2.153545, rel=.0001)
        assert proof["volume_15m"] == pytest.approx(3.776666, rel=.0001)
    if market == "KRW-AKT":
        assert proof["first_contact_confirmed"] and proof["volume_contraction"]
        assert proof["impulse_volume_5m"] > proof["volume_5m"]
    if market == "KRW-XLM":
        assert "minute_continuity" in proof["blockers"]
    for interval in ("minute1", "minute5", "minute15"):
        bars[interval].insert(0, {**bars[interval][0], "candle_date_time_utc": now.isoformat(),
            "opening_price": 1, "trade_price": 99999, "high_price": 99999,
            "candle_acc_trade_volume": 1e15})
    assert ca._completed_structure_context(bars["minute1"], bars["minute5"], bars["minute15"], now, _config()) == proof


def test_w_minute_gap_is_real_and_remains_excluded():
    now, bars = case("KRW-W")
    assert not ca.recent_minute_candles_contiguous(bars["minute1"], now)


@pytest.mark.parametrize("cause", ["gap", "volume", "wick", "anchor", "retest_lost", "retest_no_contraction"])
def test_new_structure_routes_do_not_erase_failed_evidence(cause):
    now, bars = case("KRW-AKT" if cause.startswith("retest") else "KRW-SUI")
    five = ca.completed_context_candles(bars["minute5"], 5, now)
    if cause == "gap": bars["minute1"].pop(5)
    elif cause == "volume": five[0]["candle_acc_trade_volume"] = 1
    elif cause == "wick": five[0]["high_price"] *= 1.2
    elif cause == "anchor": five[0]["trade_price"] = 1600
    elif cause == "retest_lost": five[0]["low_price"] = 1000; five[0]["trade_price"] = 1001
    elif cause == "retest_no_contraction": five[0]["candle_acc_trade_volume"] = 1e15
    proof = ca._completed_structure_context(bars["minute1"], bars["minute5"], bars["minute15"], now, _config())
    assert not proof["confirmed"]


def test_non_wb_price_breakout_reaches_delivery_policy_without_false_wb_claim(monkeypatch):
    fixture = structure_fixture(monkeypatch)
    # Valid prior OHLC bars elevate the fast upper; price clears historical
    # resistance without clearing that moving upper. No WB function is mocked.
    for i, bar in enumerate(fixture[0][2][2:6]):
        bar.update(opening_price=100.4 if i % 2 else 99.5, high_price=100.5, low_price=99.4, trade_price=100)
    c = delivery_candidate(monkeypatch, fixture)
    proof = c["survival_structure_context"]
    assert proof["confirmed"] and proof["structure_mode"] == "price_breakout"
    assert not proof["wb_confirmed"]
    assert not monitor.AlertDispatcher().candidate_delivery_reasons(c)
    text = monitor._candidate_text(c)
    assert "과거 가격대" in text and "WB 보조 확인: 미확인" in text
    for key in ("confirmed", "as_of", "volume_5m", "level"):
        invalid = copy.deepcopy(c)
        invalid["survival_structure_context"].pop(key)
        assert monitor.AlertDispatcher().candidate_delivery_reasons(invalid)


def test_contracted_first_dip_volume_has_its_own_gate(monkeypatch):
    fixture = structure_fixture(monkeypatch)
    five = fixture[0][2]
    five[2].update(opening_price=100, high_price=101.1, low_price=99.9,
                   trade_price=101, candle_acc_trade_volume=800)
    five[1].update(opening_price=100.65, high_price=101.05, low_price=100.3,
                   trade_price=101, candle_acc_trade_volume=100)
    c = delivery_candidate(monkeypatch, fixture)
    proof = c["survival_structure_context"]
    assert proof["structure_mode"] == "first_retest" and proof["retest"]
    assert .5 <= proof["volume_5m"] < 1.5
    assert proof["impulse_volume_5m"] >= 1.5
    assert ca.structure_volume_verified(c, fixture[0][0].timestamp())
    assert not monitor.AlertDispatcher().candidate_delivery_reasons(c)
    invalid = copy.deepcopy(c)
    invalid["survival_structure_context"]["volume_contraction"] = False
    assert monitor.AlertDispatcher().candidate_delivery_reasons(invalid)
