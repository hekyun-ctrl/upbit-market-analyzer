"""LBank observed 2026-11-22 23:45 forward 5m path, plus synthetic safety cases."""
import unittest
from threading import Thread
from lbank_barrier_audit import BarrierAudit,evidence
from lbank_native_entry import SOURCE

T=1795358700
def bar(t,o,h,l,c):
    return dict(start=t,open=o,high=h,low=l,close=c,quality="stream_closed")
def snap(bars):
    return dict(source=SOURCE,five_bars=bars,
                minute_bars=[dict(start=T-60,quality="stream_closed")])
PRE=bar(T-300,60711.5,61031.8,60674.8,60952.8)
FUT=[
 bar(T,60952.8,60959.2,60810,60847.6),
 bar(T+300,60847.6,60919.7,60810,60870),
 bar(T+600,60870,61003.2,60860,60901.9),
 bar(T+900,60901.9,61041.4,60873.6,60958.4),
 bar(T+1200,60958.4,60994,60894.5,60902.8),
 bar(T+1500,60902.8,60915,60805.7,60864),
]

class BarrierAuditTests(unittest.TestCase):
    def test_recorded_candidate_targets_unreached_next_30m(self):
        for strategy,target in (("breakout",61561.1),("trend_retest",61610.3)):
            a=BarrierAudit()
            s=snap([PRE])
            self.assertEqual(evidence(s,1,60924.8,60941)["status"],
                             "closed_breakout_quote_back_below")
            r=a.record(s,1,strategy,60924.8,60680,target,60941)
            self.assertEqual(r["status"],"tracking")
            self.assertEqual(a.observe(s),[])
            outcome=a.observe(snap([PRE]+FUT))[0]
            self.assertEqual(outcome["status"],"neither_first_hit_in_30m")
            self.assertEqual(outcome["future_high"],61041.4)
            self.assertEqual(outcome["future_low"],60805.7)
            self.assertEqual(outcome["first_reclaim_close"],T+1200)
            self.assertEqual(outcome["evaluated_minutes"],30)
            self.assertFalse(outcome["actual_fills_verified"])

    def test_pre_candidate_5m_wick_excluded(self):
        a=BarrierAudit()
        old=bar(T-300,60711.5,62000,60674.8,60952.8)
        a.record(snap([old]),1,"breakout",60924.8,60680,61561,60941)
        result=a.observe(snap([old]+FUT))[0]
        self.assertEqual(result["status"],"neither_first_hit_in_30m")

    def test_same_bar_both_prices_unresolved_not_profit(self):
        a=BarrierAudit(horizon=300)
        a.record(snap([PRE]),1,"breakout",60924.8,60800,61561,60941)
        o=a.observe(snap([PRE,bar(T,60952.8,61600,60700,60950)]))[0]
        self.assertEqual(o["status"],"same_bar_first_touch_ambiguous")
        self.assertFalse(o["profitability_verified"])

    def test_gap_and_wrong_quality_cannot_label_win(self):
        a=BarrierAudit(horizon=600)
        a.record(snap([PRE]),1,"breakout",60924.8,60680,61561,60941)
        o=a.observe(snap([PRE,bar(T+300,60952,60959,60900,60930)]))[0]
        self.assertEqual(o["status"],"future_data_gap_unverified")
        s=snap([PRE]);s["source"]="ticker"
        self.assertIsNone(BarrierAudit().record(s,1,"breakout",60924,60680,61561,60941))

    def test_startup_read_only_replay_never_records_live_event(self):
        a=BarrierAudit()
        output=[]
        def replay_case():
            output.append(a.record(snap([PRE]),1,"breakout",
                                   60924.8,60680,61561,60941))
        thread=Thread(target=replay_case,name="lbank-entry-replay-audit")
        thread.start()
        thread.join(timeout=2)
        self.assertFalse(thread.is_alive())
        self.assertEqual(output,[None])
        self.assertEqual(a.events,{})

    def test_short_and_duplicate_registration(self):
        a=BarrierAudit()
        s=snap([PRE])
        self.assertIsNotNone(a.record(s,-1,"breakout",60924.8,61300,60300,60941))
        self.assertIsNone(a.record(s,-1,"breakout",60922,61300,60300,60941))
        self.assertEqual(len(a.events),1)

if __name__=="__main__":
    unittest.main()
