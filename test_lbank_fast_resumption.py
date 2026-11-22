"""No premature alerts: 1m resumption only supplies qualified candidates."""
import copy
import unittest
from unittest.mock import patch
import lbank_futures_monitor as m
from lbank_fast_resumption import detect_fast_resumption


def fixture(direction=1):
    p = [60000 + direction*2*i for i in range(80)]
    # The last several completed minutes contain a pronounced pullback
    # followed by a two-candle recovery.
    baseline = p[-16]
    p[-7] = baseline-direction*115
    p[-6] = baseline-direction*100
    p[-5] = baseline-direction*75
    p[-4] = baseline-direction*25
    p[-3] = baseline+direction*5
    p[-2] = baseline+direction*30
    p[-1] = baseline+direction*65
    bars=[]
    for i,c in enumerate(p):
        op = p[i-1] if i else c
        bars.append(dict(start=180000+i*60,open=op,high=max(op,c)+4,
                         low=min(op,c)-4,close=c,volume=20. if i==79 else 10.,
                         quality="stream_closed"))
    return dict(prices=p,minute_bars=bars,quote=p[-1])


def route(d=1,regime="trend_pause"):
    return dict(ready=True,direction=d,regime=regime)


class FastResumptionTests(unittest.TestCase):
    def test_both_directions_create_only_candidate_not_approval(self):
        for direction in (1,-1):
            with self.subTest(direction=direction):
                s=fixture(direction)
                event=detect_fast_resumption(s,route(direction))
                self.assertEqual(event["status"],"candidate",event)
                self.assertFalse(event["approval"])
                self.assertEqual(event["setup"]["strategy"],"trend_retest")
                self.assertEqual(event["setup"]["direction"],direction)
                self.assertEqual(event["setup"]["source"],"closed_1m_fast_resumption")
                self.assertEqual(event["setup"]["structure_anchor"],
                                 min(x["low"] for x in s["minute_bars"][-9:-2])
                                 if direction==1 else
                                 max(x["high"] for x in s["minute_bars"][-9:-2]))

    def test_reversal_without_second_closed_minute_is_not_a_candidate(self):
        s=fixture()
        s["minute_bars"][-2].update(open=s["prices"][-2],close=s["prices"][-2])
        self.assertEqual(detect_fast_resumption(s,route())["status"],
                         "one_minute_reacceleration_missing")

    def test_one_closed_minute_can_reclaim_a_twice_tested_level(self):
        # Synthetic sealed candles, separate from the two observed incident
        # replays for which the full minute OHLCV was not available.
        for d in (1, -1):
            s = fixture(d)
            closes = s["prices"]
            base = closes[-16]
            closes[-8:] = [base + d * delta for delta in (-80, -40, -20, -78, -60, -55, -70, -12)]
            for i in range(len(closes) - 8, len(closes)):
                o, c = closes[i - 1], closes[i]
                s["minute_bars"][i].update(open=o, close=c, high=max(o, c)+4, low=min(o, c)-4)
            event = detect_fast_resumption(s, route(d))
            with self.subTest(direction=d):
                self.assertEqual(event["status"], "early_reclaim", event)
                self.assertEqual(event["reaction"], "repeated_level_reclaim")
                self.assertFalse(event["approval"])

    def test_opposite_hour_or_neutral_regime_is_not_a_candidate(self):
        for v in (route(-1),route(1,"conflicted"),route(0,"range_or_transition")):
            self.assertNotEqual(detect_fast_resumption(fixture(),v)["status"],"candidate")

    def test_data_gap_forming_or_bad_volume_fails_closed(self):
        for kind in ("gap","forming","misaligned","volume"):
            s=copy.deepcopy(fixture())
            if kind=="gap":s["minute_bars"][-5]["start"]+=60
            if kind=="forming":s["minute_bars"][-1]["quality"]="forming"
            if kind=="misaligned":s["prices"][-1]+=1
            if kind=="volume":s["minute_bars"][-1]["volume"]=1.
            with self.subTest(kind=kind):
                self.assertNotEqual(detect_fast_resumption(s,route())["status"],"candidate")

    def test_legacy_mode_keeps_existing_strategies(self):
        s=fixture()
        with patch.object(m,"ENTRY_REFINEMENT_MODE","legacy"), \
             patch.object(m,"detect_structures",return_value=[]), \
             patch.object(m,"native_entry_geometry",return_value={"atr5":30.,
                  "stop_floor_fraction":.002,"minimum_target_fraction":.006,
                  "min_range_fraction":.006}):
            d={}
            m.classify(s["prices"],native_state=s,diagnostics=d)
            self.assertEqual(d["fast_resumption"]["status"],"disabled")

    def test_capture_candidate_enters_unchanged_existing_screen(self):
        s=fixture()
        geometry={"atr5":30.,"stop_floor_fraction":.002,
                  "minimum_target_fraction":.006,"min_range_fraction":.006}
        fresh={"momentum5_pct":0.1,"momentum15_pct":0.2,
               "breakout_long":False,"breakout_short":False}
        mock_plan={"direction":1,"strategy":"trend_retest","price":s["quote"]}
        with patch.object(m,"ENTRY_REFINEMENT_MODE","capture"), \
             patch.object(m,"rank_entry_routes",return_value=route()), \
             patch.object(m,"detect_structures",return_value=[]), \
             patch.object(m,"native_entry_geometry",return_value=geometry), \
             patch.object(m,"breakout_evidence",return_value=fresh), \
             patch.object(m,"assess_candidate",return_value=mock_plan) as assess:
            diagnostic={}
            plan=m.classify(s["prices"],native_state=s,diagnostics=diagnostic)
            self.assertEqual(plan,mock_plan)
            self.assertEqual(diagnostic["fast_resumption"]["status"],"candidate")
            self.assertEqual(assess.call_count,1)
            self.assertEqual(assess.call_args.args[2]["source"],
                             "closed_1m_fast_resumption")

    def test_fast_quote_jump_cannot_bypass_original_chase_limit(self):
        s=fixture()
        s["quote"]*=1.003
        with patch.object(m,"ENTRY_REFINEMENT_MODE","capture"), \
             patch.object(m,"rank_entry_routes",return_value=route()), \
             patch.object(m,"assess_candidate") as assess:
            d={}
            self.assertIsNone(m.classify(s["prices"],native_state=s,diagnostics=d))
            self.assertEqual(d["reason"],"price_deviation")
            self.assertEqual(d["fast_resumption"]["status"],"candidate")
            assess.assert_not_called()
