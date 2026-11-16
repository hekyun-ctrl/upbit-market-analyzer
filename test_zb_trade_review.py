"""Cost and causality tests for private seven-trade forensic tooling."""
import csv
from datetime import datetime,timedelta,timezone
from pathlib import Path
import sqlite3
import tempfile
import unittest
from research.zb_trade_review import (
    fill_csv,group_fills,entry_context,model_summary,load_native,candidate_funnel,
)


class TradeForensicsTests(unittest.TestCase):
    def test_partial_fills_reconcile_gross_and_separate_fees(self):
        with tempfile.TemporaryDirectory() as d:
            path=Path(d)/"fills.csv"
            with path.open("w",newline="",encoding="utf-8") as f:
                wr=csv.DictWriter(f,fieldnames=["trade_id","time_kst","action","side",
                                      "price","quantity","realized_pnl","fee"])
                wr.writeheader()
                wr.writerow(dict(trade_id="T1",time_kst="2026-11-16 15:32:33",
                    action="open",side="short",price="64550",quantity=".1982",
                    realized_pnl="0",fee="2.5587"))
                wr.writerow(dict(trade_id="T1",time_kst="2026-11-16 15:32:33",
                    action="open",side="short",price="64550",quantity=".0519",
                    realized_pnl="0",fee=".6700"))
                wr.writerow(dict(trade_id="T1",time_kst="2026-11-16 15:58:21",
                    action="close",side="short",price="64720.9",quantity=".2501",
                    realized_pnl="-42.7420",fee="9.7120"))
            result=group_fills(fill_csv(path))[0]
            self.assertTrue(result["matched"])
            self.assertAlmostEqual(result["gross_realized_usdt"],-42.742)
            self.assertAlmostEqual(result["fee_usdt"],12.9407)
            self.assertAlmostEqual(result["estimated_net_usdt"],-55.6827)

    def test_partial_or_missing_fill_is_not_claimed_complete(self):
        with tempfile.TemporaryDirectory() as d:
            path=Path(d)/"fills.csv"
            path.write_text("trade_id,time_kst,action,side,price,quantity,realized_pnl,fee\n"
                "T,2026-11-16 15:32:33,open,short,64550,.2501,0,2\n"
                "T,2026-11-16 15:58:21,close,short,64720,.20,-20,7\n")
            self.assertFalse(group_fills(fill_csv(path))[0]["matched"])

    def test_entry_context_never_uses_candle_closed_after_actual_entry(self):
        start=1794810000
        def bar(i):
            t=start+i*300
            return dict(start=t,period=300,open=64500,high=64540,
                        low=64480,close=64500,volume=2,quality="stream_closed",
                        received_at=t+302)
        fives=[bar(i) for i in range(22)]
        enter=fives[20]["start"]+301
        trade={"first_entry_ts":enter}
        pre=entry_context(trade,fives,[])
        self.assertEqual(pre["entry_5m_bars"],20)
        self.assertEqual(pre["status"],"insufficient_historical_5m")
        later=entry_context({"first_entry_ts":fives[21]["start"]+303},fives,[])
        self.assertEqual(later["entry_5m_bars"],22)

    def test_strict_native_source_does_not_use_snapshots_or_gap(self):
        with tempfile.TemporaryDirectory() as d:
            path=Path(d)/"native.db"
            with sqlite3.connect(path) as db:
                db.executescript("""CREATE TABLE native_bars(
                 period INTEGER,start INTEGER,open REAL,high REAL,low REAL,
                 close REAL,volume REAL,turnover REAL,updated INTEGER,quality TEXT);
                 CREATE TABLE native_arrivals(
                 period INTEGER,start INTEGER,received INTEGER,source TEXT);""")
                db.execute("""INSERT INTO native_bars VALUES
                   (300,1794810000,10,11,9,10,5,50,1794810300,'stream_closed')""")
                db.execute("""INSERT INTO native_arrivals VALUES
                   (300,1794810000,1794810301,'lbank_native_1m_derived_5m')""")
                db.execute("""INSERT INTO native_bars VALUES
                   (300,1794810300,10,11,9,10,5,50,1794810600,'forming')""")
            self.assertEqual(len(load_native(path)),1)

    def test_fixed_split_excludes_training_future_outcomes(self):
        series={"events":[
            {"status":"qualified","signal_start":300,
             "outcome":{"status":"target","entry_start":600,"at":1200,
                        "entry":1000.,"model_pnl_per_btc":50.}},
            {"status":"qualified","signal_start":1500,
             "outcome":{"status":"stop","entry_start":1800,"at":2100,
                        "entry":1000.,"model_pnl_per_btc":-30.}},
        ]}
        train=model_summary(series,signal_until=900,resolve_before=900)
        test=model_summary(series,signal_from=900)
        self.assertEqual(train["resolved_nonoverlap"],0)
        self.assertEqual(train["skipped"]["split_boundary_censored"],1)
        self.assertEqual(test["resolved_nonoverlap"],1)
        self.assertEqual(test["losing"],1)
        self.assertGreater(test["max_model_drawdown_unlevered"],0.)
        self.assertEqual(test["max_consecutive_model_losses"],1)

    def test_research_candidate_funnel_counts_first_stage_without_entry(self):
        start=1794700800
        rows=[dict(start=start+i*300,period=300,open=1000.,high=1010.,
                   low=990.,close=1000.,volume=10.,quality="stream_closed",
                   received_at=start+i*300+301) for i in range(24)]
        rows[20].update(open=992.,high=995.,low=982.,close=985.,volume=10.)
        report=candidate_funnel(rows)
        self.assertEqual(report["completed_bars_checked"],4)
        self.assertGreaterEqual(
            report["lanes"]["B"]["structure_seen_low_volume"],1)
        self.assertFalse(report["entry_approved"])

    def test_ambiguous_model_outcome_excluded_from_win_rate(self):
        data={"events":[
           {"status":"qualified","outcome":{"status":"target","entry_start":300,"at":600,
                                              "model_pnl_per_btc":50.0,"entry":1000.0}},
           {"status":"qualified","outcome":{"status":"same_bar_unknown","entry_start":900,
                                              "at":900,"entry":1000.0}},
           {"status":"qualified","outcome":{"status":"stop","entry_start":600,"at":900,
                                              "model_pnl_per_btc":-30.0,"entry":1000.0}}]}
        z=model_summary(data)
        self.assertEqual(z["resolved_nonoverlap"],1)
        self.assertEqual(z["skipped"]["unknown_outcome"],1)
        self.assertEqual(z["skipped"]["overlap"],1)


if __name__=="__main__":
    unittest.main()
