"""Pure, deterministic scenario monitors. No broker orders or network."""
import sqlite3
import unittest
from types import SimpleNamespace
from unittest.mock import Mock

from lbank_scenario_alerts import evaluate_scenario, send_scenario_candidate, format_scenario


class ScenarioAlertTests(unittest.TestCase):
    def setUp(self):
        self.db = sqlite3.connect(":memory:")
        self.addCleanup(self.db.close)
        self.ledger = SimpleNamespace(db=self.db)
        self.first = 1794440000 // 300 * 300

    def history(self, *, case="B", count=21):
        rows = []
        for i in range(count):
            start = self.first + i * 300
            high, low, op, close, volume = 105, 99, 102, 102, 10
            if i == 9:
                high = 110
            if i == count - 1:
                if case == "B":
                    high, low, op, close, volume = 103, 97, 101, 98, 30
                elif case == "A":
                    high, low, op, close, volume = 110, 102, 109, 106, 18
                elif case == "C":
                    high, low, op, close, volume = 112, 101, 102, 111, 30
                elif case == "weak":
                    high, low, op, close, volume = 103, 97, 101, 98, 11
                elif case == "none":
                    high, low, op, close, volume = 105, 99, 102, 102, 10
            rows.append(dict(start=start, period=300, open=op, high=high, low=low,
                             close=close, volume=volume, quality="stream_closed",
                             received_at=start + 302))
        return rows

    def send(self, rows, **kwargs):
        sender = kwargs.pop("sender", Mock(return_value=True))
        result = send_scenario_candidate(
            self.ledger, rows,
            now=rows[-1]["start"]+303,
            started=rows[-1]["start"]-100,
            final_status="no_qualified_entry",
            send=sender, enabled=True, **kwargs)
        return result, sender

    def test_A_resistance_rejection(self):
        z = evaluate_scenario(self.history(case="A"))
        self.assertEqual((z["status"], z["scenario"]), ("candidate", "A"))
        self.assertEqual(z["resistance"], 110)
        self.assertGreater(z["stop"], 110)

    def test_B_missed_resistance_downbreak(self):
        z = evaluate_scenario(self.history(case="B"))
        self.assertEqual((z["status"], z["scenario"]), ("candidate", "B"))
        self.assertEqual(z["support"], 99)
        self.assertEqual(z["resistance"], 110)
        self.assertLess(z["close"], z["support"])

    def test_C_breakout(self):
        z = evaluate_scenario(self.history(case="C"))
        self.assertEqual((z["status"], z["scenario"]), ("candidate", "C"))
        self.assertGreater(z["close"], z["resistance"])

    def test_weak_volume_not_sent(self):
        z = evaluate_scenario(self.history(case="weak"))
        self.assertEqual(z["status"], "downbreak_volume_insufficient")
        result, sender = self.send(self.history(case="weak"))
        self.assertEqual(result, "downbreak_volume_insufficient")
        sender.assert_not_called()

    def test_no_signal_not_sent(self):
        self.assertEqual(evaluate_scenario(self.history(case="none"))["status"], "no_scenario")

    def test_insufficient_history(self):
        self.assertEqual(evaluate_scenario(self.history()[:20])["status"], "insufficient_history")

    def test_noncontiguous_window_rejected(self):
        rows = self.history()
        rows[4]["start"] += 300
        self.assertEqual(evaluate_scenario(rows)["status"], "invalid_source")

    def test_unverified_history_rejected(self):
        rows = self.history()
        rows[1]["quality"] = "gap"
        self.assertEqual(evaluate_scenario(rows)["status"], "invalid_source")

    def test_invalid_ohlc_rejected(self):
        rows = self.history()
        rows[4]["high"] = 90
        self.assertEqual(evaluate_scenario(rows)["status"], "invalid_source")

    def test_event_message_observational_and_structured(self):
        body = format_scenario(evaluate_scenario(self.history(case="B")))
        self.assertIn("저항 미도달 하방 이탈", body)
        self.assertIn("무효 관찰선", body)
        self.assertIn("매매 진입 신호 아님", body)
        self.assertIn("목표", body)
        self.assertIn("1분 스트림 합산", body)

    def test_target_ineligible_is_labeled(self):
        z = evaluate_scenario(self.history(case="B"))
        self.assertFalse(z["target_qualified"])
        self.assertIn("손익비 검증 안 됨", format_scenario(z))

    def test_send_and_cooldown(self):
        sender = Mock(return_value=True)
        rows = self.history(case="B")
        result, _ = self.send(rows, sender=sender)
        self.assertEqual(result, "B:accepted")
        result, _ = self.send(rows, sender=sender)
        self.assertEqual(result, "cooldown")
        sender.assert_called_once()

    def test_after_30_min_can_send(self):
        sender = Mock(return_value=True)
        rows = self.history(case="B")
        self.assertEqual(self.send(rows, sender=sender)[0], "B:accepted")
        future = self.history(case="B")
        shift = 6 * 300
        for row in future:
            row["start"] += shift
            row["received_at"] += shift
        self.assertEqual(self.send(future, sender=sender)[0], "B:accepted")
        self.assertEqual(sender.call_count, 2)

    def test_restart_ledger_persists(self):
        rows = self.history()
        sender = Mock(return_value=True)
        self.assertEqual(self.send(rows, sender=sender)[0], "B:accepted")
        new_ledger = SimpleNamespace(db=self.db)
        self.assertEqual(send_scenario_candidate(new_ledger, rows,
            now=rows[-1]["start"]+303, started=rows[-1]["start"]-100,
            final_status="no_qualified_entry", send=sender, enabled=True), "cooldown")
        sender.assert_called_once()

    def test_unconfirmed_not_retried(self):
        sender = Mock(side_effect=TimeoutError("timeout"))
        rows = self.history()
        self.assertEqual(self.send(rows, sender=sender)[0], "B:unconfirmed")
        self.assertEqual(self.send(rows, sender=sender)[0], "cooldown")
        sender.assert_called_once()

    def test_formal_alert_priority(self):
        rows = self.history()
        sender = Mock()
        self.assertEqual(send_scenario_candidate(self.ledger, rows,
            now=rows[-1]["start"]+303, started=rows[-1]["start"]-100,
            final_status="telegram_accepted", send=sender, enabled=True),
            "formal_alert_priority")
        sender.assert_not_called()

    def test_disabled_never_sends(self):
        rows = self.history()
        sender = Mock()
        self.assertEqual(send_scenario_candidate(self.ledger, rows,
            now=rows[-1]["start"]+303, started=rows[-1]["start"]-100,
            final_status="no_qualified_entry", send=sender, enabled=False), "disabled")
        sender.assert_not_called()

    def test_stale_not_sent(self):
        rows = self.history()
        sender = Mock()
        self.assertEqual(send_scenario_candidate(self.ledger, rows,
            now=rows[-1]["start"]+334, started=rows[-1]["start"]-100,
            final_status="no_qualified_entry", send=sender, enabled=True),
            "stale_or_before_start")
        sender.assert_not_called()

    def test_before_start_not_sent(self):
        rows = self.history()
        sender = Mock()
        self.assertEqual(send_scenario_candidate(self.ledger, rows,
            now=rows[-1]["start"]+303, started=rows[-1]["start"]+302,
            final_status="no_qualified_entry", send=sender, enabled=True),
            "stale_or_before_start")
        sender.assert_not_called()

    def test_resistance_reached_disqualifies_B(self):
        rows = self.history()
        rows[-2]["high"] = 110
        self.assertEqual(evaluate_scenario(rows)["status"], "recent_resistance_touch")


if __name__ == "__main__":
    unittest.main()

