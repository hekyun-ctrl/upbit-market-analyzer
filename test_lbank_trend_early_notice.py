"""Regression for native completed-bar informational early trend alerts."""
import unittest
from datetime import datetime, timezone

from lbank_trend_route import rank_entry_routes
from lbank_trend_early_notice import (
    trend_onset_preview, record_delivered_preview, MAX_DAILY_PREVIEWS)


def fixture(direction=1):
    now=1800000000
    last_start=now//300*300-300
    five=[]
    for i in range(30):
        start=last_start-300*(29-i)
        base=100.0
        c=100.0+direction*.30 if i==29 else base
        five.append(dict(start=start,open=base,high=max(base,c)+.005,
                         low=min(base,c)-.005,close=c,volume=2.,
                         turnover=200.,quality="stream_closed",updated=start+300))
    minutes=[]
    last1=now//60*60-60
    for i in range(30):
        start=last1-60*(29-i)
        minutes.append(dict(start=start,open=100.,close=100.,
                            high=100.01,low=99.99,quality="stream_closed"))
    state=dict(five_bars=five,minute_bars=minutes,quote=100+direction*.31,
               source="lbank_native_1m_aggregated_5m")
    return state,now+3


class EarlyTrendNoticeTests(unittest.TestCase):
    def test_early_detects_completed_onset_without_formal_entry_plan(self):
        for d in (1,-1):
            state,now=fixture(d)
            route=rank_entry_routes(state)
            with self.subTest(d=d):
                self.assertEqual(route["regime"],"impulse")
                event=trend_onset_preview(route,state,now,{})
                self.assertIsNotNone(event)
                self.assertIn("진입 미승인",event["message"])
                self.assertIn("자동주문 없음",event["message"])
                self.assertNotIn("목표가1:",event["message"])
                self.assertNotIn("손절가:",event["message"])
                self.assertIn("상승" if d==1 else "하락",event["message"])

    def test_same_bar_restart_and_cooldown_do_not_spam(self):
        state,now=fixture()
        route=rank_entry_routes(state)
        cache={}
        event=trend_onset_preview(route,state,now,cache)
        self.assertIsNotNone(event)
        record_delivered_preview(cache,event)
        self.assertEqual(cache["trend_preview_count"],1)
        self.assertIsNone(trend_onset_preview(route,state,now+1,cache))
        # New receiver instance receives the same cached alert-state dict.
        self.assertIsNone(trend_onset_preview(route,state,now+2,dict(cache)))

    def test_no_unclosed_stale_or_unverified_bar(self):
        state,now=fixture()
        route=rank_entry_routes(state)
        for kind in ("forming","stale","gapped","neutral"):
            modified=dict(state,five_bars=[dict(x) for x in state["five_bars"]])
            moment=now
            if kind=="forming":
                modified["five_bars"][-1]["quality"]="forming"
            if kind=="stale":
                moment+=150
            if kind=="gapped":
                modified["five_bars"][-1]["start"]+=300
            if kind=="neutral":
                modified["five_bars"][-1].update(
                    close=100.,high=100.005,low=99.995)
            fresh=rank_entry_routes(modified)
            self.assertIsNone(trend_onset_preview(fresh,modified,moment,{}),
                              kind)

    def test_limit_is_separate_from_formal_entry_counter(self):
        state,now=fixture()
        route=rank_entry_routes(state)
        a={"day":"2026-01-01","count":8,"last_side":"숏"}
        event=trend_onset_preview(route,state,now,a)
        self.assertIsNotNone(event)
        record_delivered_preview(a,event)
        self.assertEqual(a["count"],8)
        self.assertEqual(a["last_side"],"숏")
        later=dict(a,trend_preview_count=MAX_DAILY_PREVIEWS,
                   trend_preview_last_at=now-3600,
                   trend_preview_last_bar=-1)
        self.assertIsNone(trend_onset_preview(route,state,now,later))


if __name__=="__main__":
    unittest.main()
