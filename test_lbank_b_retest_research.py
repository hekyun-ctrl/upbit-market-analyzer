"""Deterministic B-short shadow and trend ranking tests."""
from types import SimpleNamespace
import sqlite3
import unittest

from lbank_b_retest_research import (
    _aggregate, _valid, trend_priority, evaluate_b_failed_reclaim,
    persist_b_retest_shadow, replay_b_retest,
)


class BRetestTests(unittest.TestCase):
    def rows(self, count=130, drift=-.8):
        rows=[]
        for i in range(count):
            start=300*(i+1)
            c=200-drift*-i
            rows.append(dict(start=start, period=300, open=c+.1,
                    high=c+1, low=c-1, close=c, volume=10.0,
                    quality="stream_closed", received_at=start+301))
        return rows

    def flat(self):
        rows = self.rows(130,drift=0)
        for i,row in enumerate(rows):
            row.update(open=200,high=201,low=199,close=200)
        return rows

    def test_data_quality_gaps_and_forming(self):
        x=self.rows()
        self.assertTrue(_valid(x))
        x[10]["start"]+=300
        self.assertFalse(_valid(x))
        y=self.rows();y[10]["quality"]="forming"
        self.assertFalse(_valid(y))

    def test_higher_frames_require_full_aligned_groups(self):
        r=self.rows(96)
        self.assertEqual(len(_aggregate(r,900)),31)
        self.assertEqual(len(_aggregate(r,3600)),7)

    def test_bearish_rank_shows_side_and_B_first_when_breaking(self):
        r=self.rows(130)
        z=trend_priority(r)
        self.assertEqual(z["trend"],"bearish")
        self.assertIn("숏",z["priorities"][0])
        self.assertIn("롱",z["priorities"][-1])

    def test_bullish_rank_C_long_first(self):
        x=self.rows(130,drift=.8)
        z=trend_priority(x)
        self.assertEqual(z["trend"],"bullish")
        self.assertIn("C-롱",z["priorities"][0])

    def test_mixed_regime_does_not_force_direction(self):
        rows=self.rows(120)
        # 15m trend bullish but 1h trend bearish in late recovery.
        for i,row in enumerate(rows[-10:]):
            c=row["close"]+i*5
            row.update(open=c-.1,high=c+1,low=c-1,close=c)
        z=trend_priority(rows)
        self.assertIn(z["trend"],("mixed","bearish","bullish"))
        if z["trend"]=="mixed":
            self.assertIn("관망",z["priorities"][0])

    def test_b_triggers_break_then_cannot_confirm_in_same_candle(self):
        x=self.rows()
        s=min(row["low"] for row in x[-7:-1])
        x[-1].update(open=s+.2,high=s+.5,low=s-2.5,close=s-2,volume=30)
        z=evaluate_b_failed_reclaim(x)
        self.assertEqual(z["status"],"no_recent_qualified_breakout")

    def test_b_reclaim_failure_detects_but_rejects_unknown_target(self):
        x=self.rows()
        s=min(row["low"] for row in x[-8:-2])
        x[-2].update(open=s+2,high=s+2.3,low=s-2,close=s-1.5,volume=45)
        x[-1].update(open=s-.1,high=s+.5,low=s-1.2,close=s-.6,volume=12)
        z=evaluate_b_failed_reclaim(x)
        self.assertEqual(z["trend"],"bearish")
        self.assertEqual(z["status"],"no_known_target")
        self.assertEqual(z["broken_support"],s)

    def qualified_fixture(self):
        x=self.rows(130,drift=-.08)
        x[50]["low"]=175.0
        s=min(r["low"] for r in x[-8:-2])
        x[-2].update(open=s+.2,high=s+.25,low=s-1.0,
                     close=s-.6,volume=45)
        x[-1].update(open=s-.05,high=s+.05,low=s-.8,
                     close=s-.2,volume=12)
        return x

    def test_qualified_B_short_requires_known_lower_pivot_and_net_rr(self):
        z=evaluate_b_failed_reclaim(self.qualified_fixture())
        self.assertEqual(z["status"],"qualified_shadow")
        self.assertGreaterEqual(z["net_rr_at_close"],2.0)
        self.assertLess(z["target"],z["close"])
        self.assertGreater(z["stop"],z["close"])
        self.assertLessEqual(z["structural_target_available_at"],
                             self.qualified_fixture()[-1]["received_at"])

    def test_no_target_prevents_B_entry(self):
        x=self.qualified_fixture()
        x[50]["low"]=x[50]["close"]-1
        z=evaluate_b_failed_reclaim(x)
        self.assertEqual(z["status"],"no_known_target")

    def test_no_future_data_in_B_candidate(self):
        rows=self.qualified_fixture()
        a=evaluate_b_failed_reclaim(rows)
        rows.append(dict(rows[-1],start=rows[-1]["start"]+300,
                         received_at=rows[-1]["received_at"]+300,
                         open=rows[-1]["close"],close=rows[-1]["close"],
                         high=rows[-1]["close"]+.2,low=rows[-1]["close"]-.2))
        self.assertEqual(a["status"],"qualified_shadow")
        self.assertEqual(a["confirmation_at"],rows[-2]["start"])

    def test_reclaim_invalidates(self):
        x=self.rows()
        z=evaluate_b_failed_reclaim(x)
        self.assertNotEqual(z["status"],"qualified_shadow")

    def test_insufficient_history_and_stale_no_record(self):
        x=self.rows(60)
        self.assertEqual(trend_priority(x)["status"],"insufficient_history")
        self.assertEqual(evaluate_b_failed_reclaim(x)["status"],"insufficient_history")
        d=sqlite3.connect(":memory:")
        self.addCleanup(d.close)
        ledger=SimpleNamespace(db=d)
        self.assertEqual(persist_b_retest_shadow(ledger,x,now=x[-1]["start"]+400,
                          started=0)["status"],"stale_or_before_start")

    def test_persistent_record_idempotent_no_telegram(self):
        x=self.rows()
        d=sqlite3.connect(":memory:")
        self.addCleanup(d.close)
        ledger=SimpleNamespace(db=d)
        a=persist_b_retest_shadow(ledger,x,now=x[-1]["start"]+302,started=0)
        b=persist_b_retest_shadow(ledger,x,now=x[-1]["start"]+303,started=0)
        self.assertEqual(a["status"],b["status"])
        self.assertEqual(d.execute("select count(*) from video_b_retest_shadow").fetchone()[0],1)
        self.assertIn("delivery",d.execute("select evidence from video_b_retest_shadow").fetchone()[0])

    def test_replay_counts_without_returns_claim(self):
        x=self.rows()
        z=replay_b_retest(x)
        self.assertFalse(z["production_ready"])
        self.assertIn("status_counts",z)


if __name__ == "__main__":
    unittest.main()

