"""Tests for causal first-minute B-short monitoring and immutable audit."""
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from copy import deepcopy
import sqlite3
import unittest

from lbank_b_early_1m_shadow import (
    _valid_minutes, evaluate_first_minute_break,
    persist_first_minute_shadow, read_closed_1m_window)


class FirstMinuteTests(unittest.TestCase):
    def five(self,drift=-.8):
        rows=[]
        for i in range(130):
            t=300*(i+1)
            c=200-drift*-i
            rows.append(dict(period=300,start=t,open=c+.1,
                high=c+1,low=c-1,close=c,volume=10.,
                quality="stream_closed",received_at=t+301))
        return rows

    def one(self,five):
        # Final minute occurs after the last completed 5m bar.
        start=five[-1]["start"]+300
        support=min(b["low"] for b in five[-6:])
        rows=[]
        for i in range(21):
            t=start+(i-20)*60
            o,c=(support+1,support+1) if i!=20 else (support+.5,support-3)
            rows.append(dict(period=60,start=t,open=o,close=c,
                high=max(o,c)+.2,low=min(o,c)-.2,volume=10 if i<20 else 45,
                quality="stream_closed",received_at=t+61))
        return rows

    def test_first_minute_candidate_does_not_require_subsequent_retest(self):
        five=self.five(); one=self.one(five)
        z=evaluate_first_minute_break(one,five)
        self.assertEqual(z["trend"],"bearish")
        self.assertTrue(z["early_candidate"])
        self.assertIn(z["status"],("no_known_lower_target","qualified_shadow",
                "stop_too_far","insufficient_net_rr"))
        self.assertEqual(z["minute_start"],one[-1]["start"])

    def test_subsequent_minute_cannot_replay_first_break(self):
        five=self.five(); one=self.one(five)
        n=dict(one[-1]);n["start"]+=60;n["received_at"]+=60
        n["open"]=one[-1]["close"];n["close"]=one[-1]["close"]-1
        n["high"]=n["open"]+.2;n["low"]=n["close"]-.2
        one.append(n)
        self.assertEqual(evaluate_first_minute_break(one,five)["status"],
                         "not_first_break_minute")

    def test_unsupported_direction_does_not_emit_short_candidate(self):
        five=self.five(drift=.8)
        out=evaluate_first_minute_break(self.one(five),five)
        self.assertEqual(out["status"],"not_bearish")
        self.assertFalse(out.get("early_candidate",False))

    def test_gap_and_forming_minute_are_not_evaluated(self):
        five=self.five();one=self.one(five)
        self.assertTrue(_valid_minutes(one))
        broken=deepcopy(one);broken[-5]["start"]+=60
        self.assertFalse(_valid_minutes(broken))
        broken=deepcopy(one);broken[-1]["quality"]="forming"
        self.assertFalse(_valid_minutes(broken))

    def test_future_5m_bar_rejected_not_lookahead(self):
        five=self.five();one=self.one(five)
        extra=deepcopy(five[-1]);extra["start"]+=300
        extra["received_at"]+=300
        five.append(extra)
        self.assertEqual(evaluate_first_minute_break(one,five)["status"],
                         "misaligned_5m_clock")

    def test_stale_on_restart_no_retroactive_advice(self):
        f=self.five();one=self.one(f)
        d=sqlite3.connect(":memory:");self.addCleanup(d.close)
        z=persist_first_minute_shadow(SimpleNamespace(db=d),one,f,
                                      now=one[-1]["start"]+100,started=0)
        self.assertEqual(z["status"],"stale_or_before_start")

    def test_immutable_audit_and_no_trade_or_send(self):
        f=self.five();one=self.one(f)
        d=sqlite3.connect(":memory:");self.addCleanup(d.close)
        ledger=SimpleNamespace(db=d)
        a=persist_first_minute_shadow(ledger,one,f,
                                     now=one[-1]["start"]+62,started=0)
        b=persist_first_minute_shadow(ledger,one,f,
                                     now=one[-1]["start"]+65,started=0)
        self.assertEqual(a["status"],b["status"])
        self.assertEqual(d.execute("SELECT count(*) FROM video_b_first_minute_shadow").fetchone()[0],1)
        self.assertIn("shadow_only_NO_TELEGRAM_OR_ORDER",
                      d.execute("SELECT evidence FROM video_b_first_minute_shadow").fetchone()[0])

    def test_read_only_1m_closed_no_forming_and_no_backfill(self):
        with TemporaryDirectory() as tmp:
            p=Path(tmp)/"native.sqlite3"
            db=sqlite3.connect(p)
            db.execute("""CREATE TABLE native_bars(period INTEGER,start INTEGER,
                open REAL,high REAL,low REAL,close REAL,volume REAL,
                turnover REAL,updated INTEGER,quality TEXT)""")
            db.execute("""CREATE TABLE native_arrivals(period INTEGER,start INTEGER,
                received INTEGER,source TEXT)""")
            end=180_000
            for j in range(25):
                t=end-25*60+j*60
                db.execute("INSERT INTO native_bars VALUES(?,?,?,?,?,?,?,?,?,?)",
                            (60,t,100,102,99,100,10,1000,t+59,"stream_closed"))
                db.execute("INSERT INTO native_arrivals VALUES(?,?,?,?)",
                            (60,t,t+62,"lbank_native_stream_1m"))
            db.commit();db.close()
            got=read_closed_1m_window(p,end+5)
            self.assertEqual(len(got),25)
            self.assertEqual(got[-1]["start"],end-60)
            self.assertEqual(read_closed_1m_window(p,end+60),[])


if __name__=="__main__":
    unittest.main()

