"""Safe Telegram transport diagnostics for the LBank notification monitor.

No bot tokens, chat IDs, message text, Telegram response bodies or URLs are
ever logged. Chat lookup tests credentials and access, NOT delivery of a real
message; delivery is only confirmed by successful sendMessage API response.
Ambiguous network timeouts are NOT immediately replayed to avoid duplicate
trade alerts. Existing caller-specific retries remain responsible for retries.
"""
import json
import logging
import os
import urllib.error
import urllib.parse
import urllib.request

LOG = logging.getLogger("lbank_telegram_transport")


def classify_telegram_status(code):
    return {
        400: "bad_request_or_invalid_chat",
        401: "bot_token_invalid",
        403: "chat_forbidden_or_bot_blocked",
        404: "api_endpoint_not_found",
        429: "telegram_rate_limited",
        500: "telegram_server_failure",
        502: "telegram_server_failure",
        503: "telegram_server_failure",
        504: "telegram_server_failure",
    }.get(code, "http_failure")


class TelegramTransport:
    def __init__(self, token=None, chat_id=None, opener=None):
        self.token = os.getenv("TELEGRAM_BOT_TOKEN", "") if token is None else token
        self.chat_id = os.getenv("TELEGRAM_CHAT_ID", "") if chat_id is None else chat_id
        self.opener = urllib.request.urlopen if opener is None else opener
        self.failures = 0
        self.last_class = None

    def _post(self, method, args=None, timeout=9):
        if not self.token:
            self.last_class = "missing_bot_token"
            self.failures += 1
            LOG.error("TELEGRAM_TRANSPORT_FAILURE method=%s class=%s",
                      method, self.last_class)
            return None
        encoded = {}
        for key, val in (args or {}).items():
            encoded[key] = json.dumps(val) if isinstance(val, (dict, list)) else str(val)
        payload = urllib.parse.urlencode(encoded).encode("utf-8")
        request = urllib.request.Request(
            "https://api.telegram.org/bot" + self.token + "/" + method,
            data=payload, method="POST")
        try:
            with self.opener(request, timeout=timeout) as reply:
                obj = json.load(reply)
            if isinstance(obj, dict) and obj.get("ok") is True:
                self.last_class = "success"
                return obj
            self.last_class = classify_telegram_status(
                obj.get("error_code") if isinstance(obj, dict) else None)
            self.failures += 1
            LOG.error("TELEGRAM_TRANSPORT_FAILURE method=%s class=%s",
                      method, self.last_class)
        except urllib.error.HTTPError as exc:
            self.last_class = classify_telegram_status(exc.code)
            self.failures += 1
            LOG.error("TELEGRAM_TRANSPORT_FAILURE method=%s http_status=%d class=%s",
                      method, exc.code, self.last_class)
        except (OSError, ValueError, TypeError, OverflowError) as exc:
            # URL errors can contain the token's URL. Log only exception type.
            self.last_class = "network_or_response_error"
            self.failures += 1
            LOG.warning("TELEGRAM_TRANSPORT_FAILURE method=%s class=%s exception=%s",
                        method, self.last_class, type(exc).__name__)
        return None

    def send(self, message, buttons=None):
        if not self.chat_id:
            self.last_class = "missing_chat_id"
            self.failures += 1
            LOG.error("TELEGRAM_TRANSPORT_FAILURE method=sendMessage class=%s",
                      self.last_class)
            return False
        args = {"chat_id": self.chat_id, "text": message}
        if buttons is not None:
            args["reply_markup"] = {"inline_keyboard": buttons}
        return self._post("sendMessage", args) is not None

    def health(self):
        """API-only credential/chat verification; no Telegram notification sent."""
        token_ok = self._post("getMe") is not None
        chat_ok = token_ok and bool(self.chat_id) and (
            self._post("getChat", {"chat_id": self.chat_id}) is not None)
        LOG.info("TELEGRAM_HEALTH api_token_ok=%s chat_access_ok=%s "
                 "not_a_delivery_test=true",
                 token_ok, chat_ok)
        return token_ok, chat_ok
