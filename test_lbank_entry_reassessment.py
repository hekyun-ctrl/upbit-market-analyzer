import unittest
from unittest.mock import patch

import lbank_futures_monitor as monitor
from lbank_entry_reassessment import QuoteDeviation, crossed_completed_level


class EntryReassessmentTests(unittest.TestCase):
    def test_november_22_crossing_uses_only_recorded_close_and_quotes(self):
        # 18:50 KST sealed 5m candle and its last 1m close. No later candle
        # prices, inferred minute highs, or eventual trade outcomes are inputs.
        state = dict(quote=60295.1, prices=[60249.6],
                     five_bars=[dict(start=1795340700, high=60290, low=60150)],
                     minute_bars=[dict(start=1795340940, close=60249.6)])
        route = dict(ready=True, direction=1, regime="impulse",
                     momentum_pct={"1m_3close": .1196}, reference_high_6x5m=60278,
                     shallow_pullback_continuation={"detected": True})
        self.assertEqual(crossed_completed_level(state, route, 60235.2),
                         dict(strategy="breakout", direction=1, level=60290))
        self.assertIsNone(crossed_completed_level(state, route, 60292.0))
        state["minute_bars"].append(dict(start=1795341000, high=60300))
        self.assertIsNone(crossed_completed_level(state, route, 60235.2))

    def test_scout_does_not_bypass_risk_or_supersede_a_qualified_plan(self):
        state = {"quote": 60295.1}
        crossing = {"strategy": "breakout", "direction": 1, "level": 60290}
        with patch.object(monitor, "classify") as baseline, \
             patch.object(monitor, "assess_candidate", return_value=None) as assess:
            baseline.side_effect = lambda *a, diagnostics, **k: diagnostics.update(
                reason="market_no_setup", candidates=[])
            diag = {}
            self.assertIsNone(monitor.review_setup([], state, diag, crossing))
            self.assertEqual(diag["early_candidate_reviews"][0]["source"], "quote_crossed_completed_level")
            self.assertEqual(assess.call_count, 1)
            for reason in ("price_deviation", "strategy_conflict"):
                baseline.side_effect = lambda *a, diagnostics, **k: diagnostics.update(
                    reason=reason, candidates=[])
                self.assertIsNone(monitor.review_setup([], state, {}, crossing))
            baseline.side_effect = None
            baseline.return_value = {"strategy": "trend_retest"}
            self.assertIs(monitor.review_setup([], state, {}, crossing), baseline.return_value)
            self.assertEqual(assess.call_count, 2)

    def test_full_qualified_crossing_promotes_only_in_capture(self):
        state={"quote": 60301.}
        crossing={"strategy":"breakout","direction":1,"level":60290.}
        candidate=dict(price=60301.,stop=60100.,target1=60800.,target2=61100.,
                       rr1=1.7,rr2=2.8,direction=1,strategy="breakout")
        with patch.object(monitor,"classify") as baseline, patch.object(
                monitor,"assess_candidate",return_value=dict(candidate)), patch.object(
                monitor,"ENTRY_REFINEMENT_MODE","capture"):
            baseline.side_effect=lambda *args,diagnostics,**kw: diagnostics.update(
                reason="market_no_setup",candidates=[])
            d={}
            approved=monitor.review_setup([],state,d,crossing)
            self.assertEqual(approved["entry_route"],"quote_crossing")
            self.assertEqual(approved["quote_crossing_level"],60290.)
            self.assertTrue(d["early_candidate_reviews"][0]["approval"])
            baseline.side_effect=lambda *args,diagnostics,**kw: diagnostics.update(
                reason="strategy_conflict",candidates=[])
            self.assertIsNone(monitor.review_setup([],state,{},crossing))
        with patch.object(monitor,"classify") as baseline, patch.object(
                monitor,"assess_candidate",return_value=dict(candidate)), patch.object(
                monitor,"ENTRY_REFINEMENT_MODE","legacy"):
            baseline.side_effect=lambda *args,diagnostics,**kw: diagnostics.update(
                reason="market_no_setup",candidates=[])
            self.assertIsNone(monitor.review_setup([],state,{},crossing))

    def test_impulse_crosses_without_staircase_only_with_sealed_hour(self):
        state=dict(quote=60301.,prices=[60270.],
                   five_bars=[dict(start=1795340700,high=60290.,low=60150.)],
                   minute_bars=[dict(start=1795340940,high=60280.,close=60270.)])
        route=dict(ready=True,direction=1,regime="impulse",
                   momentum_pct={"1m_3close":.12,"15m":.32,"1h":.55},
                   reference_high_6x5m=60290.,
                   shallow_pullback_continuation={"detected":False})
        self.assertEqual(crossed_completed_level(state,route,60280.),
                         dict(strategy="breakout",direction=1,level=60290.))
        route["momentum_pct"]["1h"]=-.30
        self.assertIsNone(crossed_completed_level(state,route,60280.))

    def test_a_qualified_early_review_cannot_create_a_live_watch(self):
        state = {"quote": 60849.9}
        setup = dict(strategy="trend_retest", direction=1, level=60825., structure_anchor=60800.)
        hypothesis = dict(price=60849.9, stop=60680., target1=61375., target2=61620., rr1=1.80, rr2=2.70)
        with patch.object(monitor, "classify") as baseline, \
             patch.object(monitor, "assess_candidate", return_value=hypothesis):
            baseline.side_effect = lambda *a, diagnostics, **k: diagnostics.update(
                reason="market_no_setup", candidates=[],
                fast_resumption=dict(status="early_reclaim", setup=setup))
            diagnostics = {}
            self.assertIsNone(monitor.review_setup([], state, diagnostics))
            early = diagnostics["early_candidate_reviews"][0]
            self.assertFalse(early["approval"])
            self.assertEqual(early["source"], "completed_minute_repeated_level_reclaim")
            self.assertEqual(early["risk_plan"], hypothesis)

    def test_quote_deviation_keeps_existing_gap_limit_and_restarts_stale_watch(self):
        blocked = QuoteDeviation("setup", 1795341409., 1795341300, 61097.)
        # Source: 18:56:49 diagnostic. A gap over .1% cannot be approved.
        self.assertFalse(blocked.ready(dict(quote=61097., prices=[60870.]), 1795341300, .001))
        latest = dict(quote=61126., prices=[61089.2])
        self.assertTrue(blocked.ready(latest, 1795341360, .001))
        self.assertEqual(blocked.evidence(latest, 1795341360, 1795341430.,
                                          "market_no_setup")["reassessed_quote"], 61126.)
        stale_watch = QuoteDeviation("confirmation", 1795341409., 1795341300, 61097.)
        self.assertFalse(stale_watch.ready(latest, 1795341300, .001))
        self.assertTrue(stale_watch.ready(latest, 1795341360, .001))

    def test_malformed_perpetual_quote_never_reaches_the_risk_loop(self):
        with patch("lbank_futures_monitor.ccxt.lbank") as exchange:
            exchange.return_value.fetch_ticker.return_value = dict(symbol=monitor.SYMBOL, last="inf")
            with self.assertRaisesRegex(ValueError, "invalid contract last price"):
                monitor.get_perpetual_snapshot(exchange.return_value)
