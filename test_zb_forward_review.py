from copy import deepcopy
from dataclasses import asdict
import json
from pathlib import Path
import sqlite3
from tempfile import TemporaryDirectory
import unittest

from research.zerobase.video_entry import Costs, QUOTE_RECHECK_POLICY
from research.zb_forward_review import quote_outcome, summarize_forward, forward_report


class ForwardReviewTests(unittest.TestCase):
    def setUp(self):
        self.costs=Costs(min_rr=2)
        self.model=dict(policy="locked_quote_forward_v1",revision="r1",entry=10000.,
            entered_at=602.,actual_fill=False,delivery_authorized=False,
            plan=dict(lane="bb_cci_breakout",direction=1,trigger=9990.,stop=9950.,
                      target=10200.,detected_at=601.))
        self.record=dict(bar_start=300,evaluated_at=601.,evidence=dict(
            costs=asdict(self.costs),forward_model=self.model,
            book=dict(status="usable",source="websocket",received_at=602.,bid=9999.9,ask=10000.)))

    def bars(self,n=2):
        return [dict(start=600+i*300,period=300,open=10000.,high=10010.,low=9990.,
                     close=10000.,volume=2.,quality="stream_closed",received_at=901.+i*300)
                for i in range(n)]

    def test_real_quote_after_receipt_can_be_scored_with_costs(self):
        bars=self.bars();bars[1].update(high=10210.,close=10200.)
        out=quote_outcome(self.model,bars,self.costs)
        self.assertEqual(out["status"],"target")
        self.assertAlmostEqual(out["model_pnl_per_btc"],186.88)
        report=summarize_forward([self.record],bars)
        self.assertEqual(report["cohorts"][0]["summary"]["resolved_nonoverlap"],1)
        self.assertFalse(report["unlock_authorized"])
        self.assertFalse(report["profitability_verified"])

    def test_partial_entry_candle_touch_is_unknown_even_with_later_profit(self):
        bars=self.bars();bars[0]["low"]=9940.;bars[1]["high"]=10210.
        out=quote_outcome(self.model,bars,self.costs)
        self.assertEqual(out["status"],"entry_bar_order_unknown")
        report=summarize_forward([self.record],bars)["cohorts"][0]
        self.assertEqual(report["summary"]["resolved_nonoverlap"],0)
        self.assertIn("unresolved_or_ambiguous_models",report["blockers"])

    def test_same_bar_and_gap_cannot_be_assumed_wins(self):
        bars=self.bars();bars[1].update(high=10210.,low=9940.)
        self.assertEqual(quote_outcome(self.model,bars,self.costs)["status"],"same_bar_unknown")
        self.assertEqual(quote_outcome(self.model,bars[1:],self.costs)["status"],"data_gap")

    def test_gap_stop_uses_worse_open_and_stop_slippage(self):
        bars=self.bars();bars[1].update(open=9900.,low=9890.)
        out=quote_outcome(self.model,bars,self.costs)
        self.assertEqual(out["status"],"stop")
        self.assertEqual(out["exit"],9900.)
        self.assertAlmostEqual(out["model_pnl_per_btc"],self.costs.pnl(1,10000,9900))

    def test_short_target_and_gap_limit_are_symmetric(self):
        model=deepcopy(self.model)
        model["plan"].update(direction=-1,stop=10050.,target=9800.,trigger=10010.)
        bars=self.bars();bars[1].update(open=9700.,low=9690.,high=10060.)
        out=quote_outcome(model,bars,self.costs)
        self.assertEqual(out["status"],"target")
        self.assertEqual(out["exit"],9800.)

    def test_incomplete_and_full_horizon_remain_unresolved(self):
        self.assertEqual(quote_outcome(self.model,[],self.costs)["status"],"awaiting_completed_bars")
        self.assertEqual(quote_outcome(self.model,self.bars(12),self.costs)["status"],"horizon_unresolved")

    def test_quote_before_signal_or_wrong_side_is_rejected(self):
        for mutation in ("time","price","source"):
            record=deepcopy(self.record)
            if mutation=="time":record["evidence"]["forward_model"]["entered_at"]=600.
            if mutation=="price":record["evidence"]["book"]["ask"]=10001.
            if mutation=="source":record["evidence"]["book"]["source"]="unspecified"
            report=summarize_forward([record],self.bars())
            self.assertEqual(report["excluded_records"],{"invalid_quote_provenance":1})
            self.assertFalse(report["cohorts"])

    def test_revision_changes_never_pool_forward_sample(self):
        other=deepcopy(self.record);other["evidence"]["forward_model"]["revision"]="r2"
        report=summarize_forward([self.record,other],self.bars())
        self.assertEqual(len(report["cohorts"]),2)

    def test_quote_rechecks_have_separate_cohorts_and_require_valid_geometry(self):
        rechecked = deepcopy(self.record)
        model = rechecked["evidence"]["forward_model"]
        model["policy"] = QUOTE_RECHECK_POLICY
        model["plan"].update(status="insufficient_net_rr", entry_bound=10001.)
        report = summarize_forward([self.record, rechecked], self.bars())
        self.assertEqual({x["cohort"][0] for x in report["cohorts"]},
                         {"locked_quote_forward_v1", QUOTE_RECHECK_POLICY})
        self.assertFalse(report["unlock_authorized"])
        model["plan"]["entry_bound"] = model["plan"]["trigger"]
        report = summarize_forward([rechecked], self.bars())
        self.assertEqual(report["excluded_records"], {"invalid_quote_provenance": 1})
        self.assertFalse(report["cohorts"])

    def test_readonly_ledger_report_and_missing_db(self):
        with TemporaryDirectory() as tmp:
            ledger=Path(tmp)/"ledger.db";native=Path(tmp)/"missing.db"
            self.assertTrue(forward_report(ledger,native)["no_candidates"])
            self.assertFalse(native.exists())
            with sqlite3.connect(ledger) as db:
                db.execute("CREATE TABLE video_alert_decisions (bar_start INTEGER,evaluated_at REAL,evidence TEXT)")
                db.execute("INSERT INTO video_alert_decisions VALUES (?,?,?)",
                    (300,601.,json.dumps(self.record["evidence"])))
            report=forward_report(ledger,native)
            self.assertEqual(report["cohorts"][0]["unresolved_reasons"],{"awaiting_completed_bars":1})
            self.assertFalse(native.exists())

    def test_background_snapshot_includes_forward_report_without_delivery(self):
        from lbank_video_alert import _start_review_replay_snapshot
        with TemporaryDirectory() as tmp:
            path=Path(tmp)
            worker=_start_review_replay_snapshot(path/"native.db",path)
            worker.join(timeout=5)
            self.assertFalse(worker.is_alive())
            report=json.loads((path/"zb_unverified_model_replay.json").read_text())
            self.assertFalse(report["forward_quote_model"]["unlock_authorized"])
            self.assertFalse((path/"zb_unverified_model_replay.tmp").exists())


if __name__ == "__main__":
    unittest.main()
