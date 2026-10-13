"""Offline tests for reporting actual manual LBank positions through Telegram."""
import os
import tempfile
import unittest
from unittest.mock import patch
import lbank_telegram_positions as mod


class PositionLedgerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = os.path.join(self.tmp.name, "ledger.json")
        self.ledger = mod.PositionState(self.path)

    def tearDown(self):
        self.tmp.cleanup()

    def test_open_partial_persist_restart_and_close(self):
        self.ledger.open("long", "82712.9", ".1566", "82913", "83700")
        self.assertEqual(self.ledger.position["quantity"], "0.1566")
        self.ledger.partial("0.0783")
        self.assertEqual(self.ledger.position["quantity"], "0.0783")
        other = mod.PositionState(self.path)
        self.assertEqual(other.position["quantity"], "0.0783")
        other.close()
        self.assertIsNone(mod.PositionState(self.path).position)

    def test_open_long_with_protective_stop_above_entry(self):
        self.ledger.open("long", "82712.9", ".0783", "83100", "83700")
        self.assertIn("83100", self.ledger.status())

    def test_no_widen_stop_or_negative_quantity(self):
        self.ledger.open("long", "82712.9", ".0783", "82913", "83700")
        with self.assertRaises(ValueError):
            self.ledger.move_stop("82000")
        with self.assertRaises(ValueError):
            self.ledger.partial("0.1")
        with self.assertRaises(ValueError):
            self.ledger.partial("-1")
        with self.assertRaises(ValueError):
            self.ledger.open("short","82000","0.1","83000","81000")
        self.assertEqual(self.ledger.position["quantity"], "0.0783")
        self.ledger.move_stop("83100")
        self.assertEqual(self.ledger.position["stop"], "83100")

    def test_reported_stop_warnings_and_single_notice(self):
        self.ledger.open("long","82712.9","0.0783","83100","83700")
        sample = [83390,83360,83320,83290,83210,83099]
        event=self.ledger.risk(sample)
        self.assertEqual(event[0], "신고 손절가 도달/이탈")
        self.ledger.acknowledge_risk(event[0])
        self.assertIsNone(self.ledger.risk(sample))
        self.ledger.move_stop("83120")
        self.assertIsNotNone(self.ledger.risk(sample))

    def test_early_risk_requires_repeated_confirmation(self):
        self.ledger.open("short", "82000", ".05", "82200", "81500")
        bars=[81500,81540,81600,81800,81950,82110]
        self.assertIsNone(self.ledger.risk(bars))
        event=self.ledger.risk(bars)
        self.assertIsNotNone(event)
        self.assertIn("근접",event[0])

    def test_confirmed_button_half_close_and_stale_buttons(self):
        self.ledger.open("long", "82712.9", ".0783", "83100", "83700")
        output=[]
        with patch.object(mod,"AUTHORIZED_CHAT","123"):
            bot=mod.TelegramBot(self.ledger)
            bot.enabled=True
            bot.send=lambda message,buttons=None: (output.append((message,buttons)) or True)
            bot.api=lambda method,args=None: True
            rev=self.ledger.state["revision"]
            msg={"message":{"chat":{"id":123}},"data":f"askhalf:{rev}","id":"q1"}
            bot.dispatch_update({"callback_query":msg})
            self.assertEqual(self.ledger.position["quantity"],"0.0783")
            bot.dispatch_update({"callback_query":{"message":{"chat":{"id":123}},"data":f"confirm:{rev}","id":"q2"}})
            self.assertEqual(self.ledger.position["quantity"],"0.0392")
            bot.dispatch_update({"callback_query":{"message":{"chat":{"id":123}},"data":f"confirm:{rev}","id":"q3"}})
            self.assertEqual(self.ledger.position["quantity"],"0.0392")
            self.assertTrue(any("지난 버튼" in x[0] for x in output))

    def test_unauthorized_chat_cannot_modify_report(self):
        self.ledger.open("long", "82712.9", ".0783", "83100", "83700")
        with patch.object(mod,"AUTHORIZED_CHAT","123"):
            bot=mod.TelegramBot(self.ledger)
            bot.enabled=True
            bot.send=lambda *args,**kwargs: True
            bot.dispatch_update({"message":{"chat":{"id":999},"text":"/close","from":{"id":999}}})
            self.assertIsNotNone(self.ledger.position)

    def test_text_command_and_funding_no_exchange_access(self):
        with patch.object(mod,"AUTHORIZED_CHAT","123"):
            bot=mod.TelegramBot(self.ledger)
            bot.enabled=True
            message=bot.dispatch_command("/open long 82712.9 0.0783 83100 83700")
            self.assertIn("잔여 수량",message)
            self.assertIn("0.0783",bot.dispatch_command("/status"))
            self.assertIn("전량",bot.dispatch_command("/close"))
            self.assertIsNone(self.ledger.position)


if __name__ == "__main__":
    unittest.main()
