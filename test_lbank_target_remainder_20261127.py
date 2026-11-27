"""Regression: target observation is not liquidation or permission to loosen a guard."""
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from lbank_telegram_positions import PositionState, format_position_target_alert
from lbank_signal_risk import SignalRiskTracker, TrackedSignal, format_target_alert


class TargetRemainderTests(unittest.TestCase):
    def test_reported_remainder_protection_continues_after_delivered_target(self):
        for side, stop, target in (("LONG", 59800, 60300), ("SHORT", 60200, 59700)):
            with self.subTest(side=side), tempfile.TemporaryDirectory() as folder:
                path = str(Path(folder) / "position.json")
                ledger = PositionState(path)
                ledger.open(side, 60000, .1, stop, target)
                ledger.acknowledge_target()
                ledger.partial(".05")
                ledger = PositionState(path)
                self.assertTrue(ledger.position["target_notice"])
                self.assertEqual(float(ledger.position["quantity"]), .05)
                self.assertIsNone(ledger.target_reached([target]))
                with patch("lbank_telegram_positions.assess_thesis", return_value={"invalidated":False}), patch("lbank_telegram_positions.profit_observe", return_value=({"guard":60050 if side=="LONG" else 59950}, "profit_guard_updated")) as observer:
                    self.assertEqual(ledger.profit_check(target, 1000), "profit_guard_updated")
                    self.assertEqual(observer.call_args.kwargs["minimum_guard"], stop)
                self.assertEqual(float(ledger.position["quantity"]), .05)
                self.assertEqual(float(ledger.position["stop"]), stop)

    def test_manual_notice_preserves_reported_stop_and_guard(self):
        for side, stop, target, guard, expected in (("LONG", 60180, 60300, 60150, "60,180.0"), ("SHORT", 59820, 59700, 59850, "59,820.0")):
            p=dict(side=side,entry="60000",stop=str(stop),target=str(target),profit_state={"guard":guard},target_stage=1)
            msg=format_position_target_alert(p,target,"test")
            self.assertIn(expected,msg)
            self.assertIn("전량 청산 신호가 아닙니다",msg)
            self.assertIn("/partial",msg)
            p["target_stage"]=2
            self.assertIn("추세 종료 확정이 아닙니다",format_position_target_alert(p,target,"test"))

    def test_reference_tp1_never_loosens_existing_stop(self):
        for d in (1,-1):
            entry=60000
            sig=TrackedSignal(1,d,entry,entry+d*180,entry+d*300,0,target2=entry+d*500,runner_enabled=True,profit_state={"guard":entry+d*150})
            tracker=SignalRiskTracker()
            tracker.signals=[sig]
            msg=format_target_alert(sig,sig.target1,"test")
            self.assertIn(f"{sig.stop:,.1f}",msg)
            old=sig.stop
            tracker.acknowledge(sig,"target1_reached",now=123)
            self.assertEqual(sig.stop,old)
            self.assertTrue(sig.target1_notified)
            self.assertIn(sig,tracker.signals)

    def test_reference_guard_can_strengthen_protection(self):
        for d in (1,-1):
            sig=TrackedSignal(1,d,60000,60000-d*200,60000+d*300,0,target2=60000+d*500,runner_enabled=True,profit_state={"guard":60000+d*100})
            tracker=SignalRiskTracker()
            tracker.signals=[sig]
            tracker.acknowledge(sig,"target1_reached",now=123)
            self.assertEqual(sig.stop,60000+d*100)


if __name__ == "__main__":
    unittest.main()
