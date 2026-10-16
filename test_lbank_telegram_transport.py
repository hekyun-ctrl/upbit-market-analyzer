"""Offline Telegram health/error classification tests, no network and no secrets."""
import io
import json
import unittest
import urllib.error

from lbank_telegram_transport import TelegramTransport, classify_telegram_status


class TelegramTransportTests(unittest.TestCase):
    def test_token_and_chat_health_checks_without_sending_message(self):
        calls = []
        def opener(request, timeout):
            calls.append((request.full_url.rsplit("/", 1)[-1], request.data))
            return io.BytesIO(json.dumps({"ok": True, "result": {"id": 1}}).encode())
        transport = TelegramTransport("secretTOKEN", "chat123", opener)
        self.assertEqual(transport.health(), (True, True))
        self.assertEqual([x[0] for x in calls], ["getMe", "getChat"])
        self.assertNotIn("sendMessage", [x[0] for x in calls])
        self.assertIn(b"chat123", calls[1][1])

    def test_send_success_reports_actual_telegram_api_acceptance(self):
        def opener(request, timeout):
            self.assertEqual(request.full_url.rsplit("/", 1)[-1], "sendMessage")
            return io.BytesIO(b'{"ok":true,"result":{"message_id":5}}')
        transport = TelegramTransport("valid", "chat", opener)
        self.assertTrue(transport.send("[LBank] test only"))
        self.assertEqual(transport.last_class, "success")

    def test_send_failure_401_identifies_token_without_exposing_secret(self):
        def opener(request, timeout):
            raise urllib.error.HTTPError(request.full_url, 401, "unauthorized", {}, None)
        transport = TelegramTransport("VERY_SECRET_TOKEN", "PRIVATE_CHAT_ID", opener)
        with self.assertLogs("lbank_telegram_transport", "ERROR") as cm:
            self.assertFalse(transport.send("sensitive"))
        self.assertEqual(transport.last_class, "bot_token_invalid")
        self.assertNotIn("VERY_SECRET_TOKEN", " ".join(cm.output))
        self.assertNotIn("PRIVATE_CHAT_ID", " ".join(cm.output))
        self.assertNotIn("sensitive", " ".join(cm.output))

    def test_403_and_429_report_causes(self):
        self.assertEqual(classify_telegram_status(403), "chat_forbidden_or_bot_blocked")
        self.assertEqual(classify_telegram_status(429), "telegram_rate_limited")

    def test_network_timeout_not_immediately_retried_to_avoid_duplicate(self):
        calls = []
        def opener(request, timeout):
            calls.append(request)
            raise TimeoutError("ambiguous send")
        transport = TelegramTransport("test", "test", opener)
        with self.assertLogs("lbank_telegram_transport", "WARNING"):
            self.assertFalse(transport.send("test"))
        self.assertEqual(len(calls), 1)

    def test_missing_credentials_are_reported_without_calls(self):
        def no_call(*args, **kwargs):
            self.fail("must not call network")
        transport = TelegramTransport("", "", no_call)
        with self.assertLogs("lbank_telegram_transport", "ERROR"):
            self.assertEqual(transport.health(), (False, False))
            self.assertFalse(transport.send("a"))



    def test_one_time_probe_sends_one_non_trade_message_across_restart(self):
        import os
        import tempfile
        methods = []
        def opener(request, timeout):
            method = request.full_url.rsplit("/", 1)[-1]
            methods.append(method)
            return io.BytesIO(b'{"ok":true,"result":{"message_id":13}}')
        with tempfile.TemporaryDirectory() as tmp:
            marker = os.path.join(tmp, "probe")
            a = TelegramTransport("token", "chat", opener)
            self.assertTrue(a.startup_probe_once(marker))
            b = TelegramTransport("token", "chat", opener)
            self.assertTrue(b.startup_probe_once(marker))
            self.assertEqual(methods.count("sendMessage"), 1)
            self.assertTrue(os.path.exists(marker))


if __name__ == "__main__":
    unittest.main()
