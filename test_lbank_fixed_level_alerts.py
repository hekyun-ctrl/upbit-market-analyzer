"""Safety checks for exact user-provided BTCUSDT price crossings.

No exchange calls, Telegram transport, or order execution.
"""
import sqlite3
import unittest
from types import SimpleNamespace
from unittest.mock import Mock

from lbank_fixed_level_alerts import (
    LOWER_SUPPORT, UPPER_RECOVERY,
    evaluate_level_cross, format_level_alert, send_level_alert,
)


class FixedLevelNoticeTests(unittest.TestCase):
    def setUp(self):
        self.db = sqlite3.connect(":memory:")
        self.addCleanup(self.db.close)
        self.ledger = SimpleNamespace(db=self.db)
        self.first = 1794700000 // 300 * 300

    def bars(self, period=60, before=62600., after=62490., shift=0):
        start = self.first + shift
        return [
            dict(start=start+i*period, period=period, quality="stream_closed",
                 open=c, high=c+250., low=c-250., close=c, volume=10.,
                 received_at=start+(i+1)*period+2.)
            for i,c in enumerate((before,after))
        ]

    def send(self, rows, sender=None, started=0, **kw):
        sender = sender if sender is not None else Mock(return_value=True)
        now = rows[-1]["start"]+rows[-1]["period"]+3
        status = send_level_alert(self.ledger, rows, rows[-1]["period"],
                                  now=now, started=started,
                                  send=sender, enabled=True, **kw)
        return status, sender

    def test_down_early_and_up_recovery_require_crossover(self):
        down = evaluate_level_cross(self.bars(),60,self.first+123)
        self.assertEqual((down["status"],down["direction"],down["level"]),
                         ("candidate","down",LOWER_SUPPORT))
        up = evaluate_level_cross(self.bars(before=63000.,after=63080.),60,self.first+123)
        self.assertEqual((up["status"],up["direction"],up["level"]),
                         ("candidate","up",UPPER_RECOVERY))
        self.assertEqual(evaluate_level_cross(
            self.bars(before=62500.,after=62300.),60,self.first+123)["status"],
            "no_fresh_cross")

    def test_five_minute_confirm_uses_only_closed_candles(self):
        a=self.bars(period=300)
        self.assertEqual(evaluate_level_cross(a,300,self.first+603)["status"],"candidate")
        self.assertEqual(self.send(a)[0],"down:accepted")
        message=format_level_alert(evaluate_level_cross(a,300,self.first+603))
        self.assertIn("5분 마감",message)
        self.assertIn("62,525 하향",message)
        self.assertIn("진입가: 미확정",message)
        self.assertIn("손절가: 미확정",message)
        self.assertIn("목표가: 미확정",message)
        self.assertIn("진입 미승인",message)

    def test_one_minute_and_five_minute_have_separate_notices(self):
        a=self.bars(period=60)
        b=self.bars(period=300)
        sender=Mock(return_value=True)
        self.assertEqual(self.send(a,sender)[0],"down:accepted")
        self.assertEqual(self.send(b,sender)[0],"down:accepted")
        self.assertEqual(sender.call_count,2)

    def test_duplicate_restart_and_cooldown(self):
        rows=self.bars()
        sender=Mock(return_value=True)
        self.assertEqual(self.send(rows,sender)[0],"down:accepted")
        self.assertEqual(self.send(rows,sender)[0],"cooldown")
        shifted=self.bars(shift=5*60)
        self.assertEqual(self.send(shifted,sender)[0],"cooldown")
        self.assertEqual(self.db.execute(
            "SELECT count(*) FROM video_fixed_level_notice").fetchone()[0],1)
        self.assertEqual(sender.call_count,1)
        later=self.bars(shift=16*60)
        self.assertEqual(self.send(later,sender)[0],"down:accepted")

    def test_no_replay_and_gaps_and_forming(self):
        sender=Mock()
        rows=self.bars()
        self.assertEqual(self.send(rows,sender,started=rows[-1]["start"]+60)[0],
                         "before_worker_start")
        rows[0]["start"]-=60
        self.assertEqual(self.send(rows,sender)[0],"invalid_source")
        rows=self.bars();rows[1]["quality"]="forming"
        self.assertEqual(self.send(rows,sender)[0],"invalid_source")
        rows=self.bars();rows[1]["received_at"]=rows[1]["start"]+59
        self.assertEqual(self.send(rows,sender)[0],"invalid_source")
        sender.assert_not_called()

    def test_timeout_reserved_without_retry(self):
        sender=Mock(side_effect=TimeoutError("timeout"))
        a=self.bars()
        self.assertEqual(self.send(a,sender)[0],"down:unconfirmed")
        self.assertEqual(self.send(a,sender)[0],"cooldown")
        sender.assert_called_once()

    def test_invalid_ohlc_does_not_send(self):
        rows=self.bars()
        rows[-1]["low"]=rows[-1]["high"]+1
        self.assertEqual(self.send(rows)[0],"invalid_source")


if __name__ == "__main__":
    unittest.main()
