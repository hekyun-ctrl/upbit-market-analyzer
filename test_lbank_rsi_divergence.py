import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import lbank_rsi_divergence as r
import lbank_futures_monitor as m
from lbank_native_entry import SOURCE
import test_lbank_native_entry as native_tests


def sample(direction=1):
    bars = [dict(start=i*300,open=100.,high=100.3,low=99.7,close=100.,
                 volume=1.,quality="stream_closed") for i in range(30)]
    bars[20]["low"], bars[27]["low"] = 99., 98.9
    values = [50.]*16
    values[6], values[13], values[-2], values[-1] = 20.,30.,40.,45.
    if direction==-1:
        for b in bars:
            b["open"],b["close"],b["high"],b["low"] = 200-b["open"],200-b["close"],200-b["low"],200-b["high"]
        values = [100-x for x in values]
    return {"source":SOURCE,"five_bars":bars}, values


class DivergenceTests(unittest.TestCase):
    def test_two_confirming_bars_are_required_and_plateaus_not_pivots(self):
        bars = [{"low":x} for x in [5,4,1,3,4]]
        self.assertEqual(r.pivots(bars[:4],"low"),[])
        self.assertEqual(r.pivots(bars,"low"),[2])
        bars[3]["low"] = 1
        self.assertEqual(r.pivots(bars,"low"),[])

    def test_rsi_is_aligned_to_price_pivot_and_both_directions(self):
        for direction in (1,-1):
            state, values = sample(direction)
            with patch.object(r,"wilder_rsi_series",return_value=values):
                result = r.analyze(state)
                self.assertTrue(result["ready"])
                self.assertEqual(len(result["divergences"]),1)
                event = result["divergences"][0]
                self.assertEqual(event["direction"],direction)
                self.assertEqual(event["rsi1"],20 if direction==1 else 80)
                self.assertEqual(event["rsi2"],30 if direction==1 else 70)
                self.assertEqual(event["available_at"],30*300)

    def test_gap_forming_and_broken_extreme_are_not_live_context(self):
        for mutation in (lambda s:s["five_bars"].pop(10),
                         lambda s:s["five_bars"][-1].update(quality="forming"),
                         lambda s:s["five_bars"][-1].update(close=float("nan"))):
            state,_ = sample()
            mutation(state)
            self.assertFalse(r.analyze(state)["ready"])
        state,values = sample()
        state["five_bars"][-1]["low"] = 98.8
        with patch.object(r,"wilder_rsi_series",return_value=values):
            self.assertEqual(r.analyze(state)["divergences"],[])

    def test_one_minute_confirmation_must_close_after_discovery(self):
        event = {"direction":1,"available_at":9000,"structure_anchor":98.9,"level":99.,"zone":.5}
        bars = [dict(start=8760+i*60,open=99.05,high=99.2,low=99.,close=99.1,
                     quality="stream_closed") for i in range(4)]
        bars[-1].update(high=99.55,close=99.5)
        self.assertTrue(r.confirmation({"minute_bars":bars},event))
        self.assertFalse(r.confirmation({"minute_bars":bars},dict(event,available_at=9060)))
        bars[-1]["close"] = 99.06
        self.assertFalse(r.confirmation({"minute_bars":bars},event))

    def test_native_replay_never_backdates_signal_to_pivot(self):
        raw = json.loads((Path(__file__).parent/"research/rsi-divergence-native-20261018.json").read_text())["bars"]
        seen = set()
        for n in range(30,len(raw)+1):
            result = r.analyze({"source":SOURCE,"five_bars":raw[max(0,n-250):n]})
            for event in result.get("divergences",[]):
                self.assertLessEqual(event["available_at"],raw[n-1]["start"]+300)
                self.assertEqual(event["available_at"],event["pivot2"]+900)
                seen.add(event["id"])
        self.assertTrue(seen)

    def test_optional_context_failure_cannot_cancel_plan(self):
        plan = {"direction":1,"price":100.}
        with patch.object(r,"analyze",side_effect=RuntimeError("study failure")):
            self.assertIs(r.annotate(plan,{}),plan)


class LiveNonInterferenceTests(unittest.TestCase):
    def test_original_native_qualification_confirmation_and_gate_are_identical(self):
        fixture = native_tests.NativeEntryTests()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        state,_ = fixture.snapshot()
        # Annotation invariance is tested on a complete higher-priority context;
        # a lost trigger with missing priority is now intentionally rejected.
        state["rsi5"]["rsi"] = [83.,84.,85.]
        untouched = copy.deepcopy(state)
        annotated = m.classify(state["prices"],native_state=state)
        from test_lbank_entry_quality import future_confirmation_state
        future = future_confirmation_state(state, annotated)
        future['quote'] = annotated['price'] + 25.  # preserve the existing extension cap
        with patch.object(m,"annotate_rsi_context",side_effect=lambda p,s:p):
            baseline = m.classify(state["prices"],native_state=state)
            confirm_base = m.confirm_watch(future["prices"],baseline,180,native_state=future)
        self.assertIsNotNone(confirm_base)
        self.assertIsNotNone(baseline)
        self.assertEqual({k:v for k,v in annotated.items() if k!="rsi_context"},baseline)
        confirm_new = m.confirm_watch(future["prices"],annotated,180,native_state=future)
        self.assertEqual({k:v for k,v in confirm_new.items() if k!="rsi_context"},confirm_base)
        self.assertEqual(state,untouched)
        gate = {"day":"1970-01-01","count":0,"last_time":1000,"last_side":"롱"}
        for t in (1001,4000,9000):
            self.assertEqual(m.alert_gate(t,"롱",gate.copy(),plan=annotated),m.alert_gate(t,"롱",gate.copy(),plan=baseline))
        note = m.format_entry_alert(annotated,"2026-10-18T12:00:00Z")
        self.assertIn("보조:",note)
        self.assertLessEqual(len(note.splitlines()),10)


class PaperStudyTests(unittest.TestCase):
    def test_paper_double_confirmation_restart_dedup_and_cost_outcome(self):
        with tempfile.TemporaryDirectory() as directory:
            path = directory+"/study.sqlite3"
            study = r.DivergenceStudy(path,1000)
            setup = {"id":"div:1:1:2","strategy":"rsi_divergence","direction":1}
            context = {"ready":True,"setups":[setup],"divergences":[setup]}
            state = {"prices":[100.]*65,"rsi5":{"rsi":[30.,31.]},"minute_bars":[{}]}
            plan = dict(direction=1,price=100.,stop=99.,target1=102.,target2=104.,
                        quantity_btc=1.,net_loss=1.15,strategy="rsi_divergence")
            calc = lambda *args,**kw:plan
            gate = lambda *args:(True,"ok",{})
            with patch.object(r,"analyze",return_value=context),patch.object(r,"confirmation",return_value=True):
                self.assertEqual(study.step(1000,100,state,calc,gate,baseline_reason="market_no_setup")["decisions"][0]["reason"],"paper_watch")
                study.step(1060,100,state,calc,gate,baseline_reason="market_no_setup")
                self.assertEqual(study.book.summary()["levels"]["signals"],1)
                # Simulate interrupted status update after add() already committed.
                with study.book.db:
                    study.book.db.execute("UPDATE rsi_candidates SET status='watch'")
                study.book.db.close()
                study = r.DivergenceStudy(path,1000)
                self.addCleanup(study.book.db.close)
                study.step(1120,100,state,calc,gate,baseline_reason="qualified")
                self.assertEqual(study.book.summary()["levels"]["signals"],1)
                with patch.object(r,"analyze",return_value={"ready":False,"setups":[]}):
                    study.step(1180,104,None,calc,gate,baseline_reason="native_data_unavailable")
                stats = study.book.summary()["levels"]
                self.assertEqual(stats["closed"],1)
                self.assertLess(stats["net_usdt_model"],3.)
                self.assertIn("계좌수익 아님",study.report_line())


if __name__=="__main__":
    unittest.main()
