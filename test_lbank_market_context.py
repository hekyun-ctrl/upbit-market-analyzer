import copy
import json
import sqlite3
import unittest
from datetime import datetime, timezone
from unittest.mock import patch

import lbank_market_context as m
import lbank_futures_monitor as monitor
from lbank_performance import PerformanceBook
from test_lbank_performance import plan


def point(at=10000, **updates):
    p = dict(source=m.SOURCE,market_at=at,btc_at=at,usdt_at=at,observed_at=at,
             total_usd=1_000_000.,btc_d=60.,usdt_d=6.,usdt_cap_usd=60000.,
             btc_usd=80000.,usdt_usd=1.,usdt_supply=60000.)
    p.update(updates)
    return p


def payloads(at=10000):
    stamp = datetime.fromtimestamp(at,timezone.utc).isoformat()
    g = {"data":{"updated_at":at,"total_market_cap":{"usd":1_000_000.},
                 "market_cap_percentage":{"btc":60.,"usdt":6.}}}
    c = [{"id":"bitcoin","current_price":80000.,"last_updated":stamp},
         {"id":"tether","current_price":1.,"circulating_supply":60000.,"last_updated":stamp}]
    return g,c


class MarketContextTests(unittest.TestCase):
    def test_parse_source_time_shares_and_invalid_feed(self):
        g,c = payloads()
        p = m.parse_snapshot(g,c,10020)
        self.assertEqual(p["usdt_cap_usd"],60000.)
        self.assertEqual(p["market_at"],10000)
        for now in (12000,9000):
            with self.assertRaises(ValueError):
                m.parse_snapshot(g,c,now)
        g["data"]["market_cap_percentage"]["btc"] = 99
        with self.assertRaises(ValueError):
            m.parse_snapshot(g,c,10020)
        g,c = payloads()
        c[1]["current_price"] = float("nan")
        with self.assertRaises(ValueError):
            m.parse_snapshot(g,c,10020)

    def test_dominance_change_is_percentage_points_and_does_not_imply_btc_rise(self):
        rows = [point(10000+i*300) for i in range(13)]
        rows[-1] = point(13600,btc_d=61.,usdt_d=6.2,total_usd=970000.,btc_usd=79000.,usdt_cap_usd=60140.)
        result = m.summarize(rows,13620)
        self.assertEqual(result["horizons"]["1h"]["btc_d_pp"],1.)
        self.assertEqual(result["alignment_direction"],-1)
        self.assertIn("시장 약세 속 BTC 상대강세",result["notes"])
        self.assertFalse(result["flow_inferred"])

    def test_denominator_effect_and_supply_change_are_not_labelled_inflows(self):
        rows = [point(10000+i*300) for i in range(13)]
        rows[-1] = point(13600,total_usd=950000.,usdt_d=60000/950000*100,
                         btc_usd=78000.,usdt_supply=61000.)
        result = m.summarize(rows,13600)
        self.assertIn("USDT 비중 상승에 전체 시총 감소 동반",result["notes"])
        self.assertIn("USDT 공급 변화 동반·자금유입과 동일하지 않음",result["notes"])

    def test_warmup_gap_stale_and_provider_mixing_do_not_invent_trend(self):
        self.assertEqual(m.summarize([point()],10020)["horizons"],{})
        self.assertEqual(m.summarize([point(),point(13600)],13620)["horizons"],{})
        rows = [point(10000+i*300) for i in range(13)]
        rows[0]["source"] = "different_universe"
        rows = rows[:1]+rows[3:]
        self.assertNotIn("1h",m.summarize(rows,13620)["horizons"])
        self.assertFalse(m.summarize([point()],12000)["ready"])

    def test_cached_snapshot_expires_and_annotation_cannot_change_entry(self):
        reader = m.MarketContextThread()
        reader._context = m.summarize([point(usdt_usd=.995)],10020)
        p = dict(plan(),side="롱")
        with patch.object(m,"fetch_json",side_effect=AssertionError("network on main loop")):
            annotated = m.annotate(p,reader,10030)
        self.assertEqual({k:v for k,v in annotated.items() if k!="market_context"},p)
        self.assertIn("달러 이탈 관측",annotated["market_context"]["line"])
        annotated["market_context"]["usdt_usd"] = 1
        self.assertEqual(reader.snapshot(10030)["usdt_usd"],.995)
        self.assertFalse(reader.snapshot(12000)["ready"])
        with patch.object(reader,"snapshot",side_effect=RuntimeError("source failure")):
            self.assertIs(m.annotate(p,reader,10030),p)
        gate = {"day":None,"count":0,"last_time":None,"last_side":None}
        self.assertEqual(monitor.alert_gate(10030,"롱",gate.copy(),plan=annotated),
                         monitor.alert_gate(10030,"롱",gate.copy(),plan=p))

    def test_repeated_source_update_is_not_a_new_bar_and_regression_rejected(self):
        db = sqlite3.connect(":memory:")
        self.addCleanup(db.close)
        db.execute("CREATE TABLE snapshots(ts REAL PRIMARY KEY,detail TEXT NOT NULL)")
        reader = m.MarketContextThread()
        g,c = payloads()
        with patch.object(m,"fetch_json",side_effect=[g,c,g,c]):
            reader.poll_once(db,10020)
            reader.poll_once(db,10100)
        self.assertEqual(db.execute("SELECT COUNT(*) FROM snapshots").fetchone()[0],1)
        self.assertEqual(reader.snapshot(10100)["regime"],"warming_up")
        old_g,old_c = payloads(9900)
        with patch.object(m,"fetch_json",side_effect=[old_g,old_c]):
            with self.assertRaises(ValueError):
                reader.poll_once(db,10100)

    def test_entry_cohort_is_frozen_not_relabelled_with_future_data(self):
        book = PerformanceBook(":memory:",1000)
        self.addCleanup(book.db.close)
        ctx = dict(ready=True,regime="up_alignment",alignment_direction=1)
        p = dict(plan(),market_context=ctx)
        book.add(1,p,1000,fee=0,slippage=0,funding=0)
        ctx["alignment_direction"] = -1
        book.observe(1060,104)
        result = book.market_context_summary()
        group = result["policies"]["levels"]["aligned"]
        self.assertEqual(group["signals"],1)
        self.assertEqual(group["completed"],1)
        self.assertEqual(group["mean_net_usdt"],3.)
        self.assertFalse(result["causal_improvement_verified"])


if __name__ == "__main__":
    unittest.main()
