"""Safe Telegram transport diagnostics for the LBank notification monitor.

No bot tokens, chat IDs, message text, Telegram response bodies or URLs are
ever logged. Chat lookup tests credentials and access, NOT delivery of a real
message. A successful sendMessage response proves API acceptance. An entry
audit records the message identifier and whether Telegram echoed the expected
destination, while leaving recipient receipt unverified.
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

    def send(self, message, buttons=None, *, entry_audit=None):
        if not self.chat_id:
            self.last_class = "missing_chat_id"
            self.failures += 1
            LOG.error("TELEGRAM_TRANSPORT_FAILURE method=sendMessage class=%s",
                      self.last_class)
            if entry_audit is not None:
                self._log_entry_response(*entry_audit, None)
            return False
        args = {"chat_id": self.chat_id, "text": message}
        if buttons is not None:
            args["reply_markup"] = {"inline_keyboard": buttons}
        response = self._post("sendMessage", args)
        if entry_audit is not None:
            self._log_entry_response(*entry_audit, response)
        return response is not None

    def _log_entry_response(self, stream, reference, response):
        if response is None:
            LOG.warning("TELEGRAM_ENTRY_UNCONFIRMED stream=%s reference=%d class=%s",
                        stream, reference, self.last_class)
            return
        message = response.get("result")
        message = message if isinstance(message, dict) else {}
        chat = message.get("chat")
        chat = chat if isinstance(chat, dict) else {}
        message_id = message.get("message_id")
        if type(message_id) is not int or message_id < 0:
            message_id = None
        chat_type = chat.get("type")
        if chat_type not in ("private", "group", "supergroup", "channel"):
            chat_type = "unverified"
        destination = "unverified"
        expected = str(self.chat_id).strip()
        if expected.startswith("@"):
            username = chat.get("username")
            if isinstance(username, str):
                destination = "match" if username.casefold() == expected[1:].casefold() else "mismatch"
        elif type(chat.get("id")) is int:
            try:
                destination = "match" if chat["id"] == int(expected) else "mismatch"
            except ValueError:
                pass
        LOG.info("TELEGRAM_ENTRY_ACCEPTED stream=%s reference=%d message_id=%s "
                 "destination=%s chat_type=%s recipient_receipt_verified=false",
                 stream, reference, message_id, destination, chat_type)

    def startup_probe_once(self, marker_path="/data/lbank_telegram_transport_probe_v1"):
        """Health-check and send ONE clearly labeled non-trade message.

        Reserve the marker with O_EXCL *before* sending, avoiding repeated pings
        during overlapping Railway deployments. Missing access is logged and
        does not interfere with the ongoing market monitor. Never sends orders.
        """
        token_ok, chat_ok = self.health()
        if not (token_ok and chat_ok):
            LOG.warning("TELEGRAM_STARTUP_PROBE_BLOCKED class=%s",
                        self.last_class or "chat_unavailable")
            return False
        directory = os.path.dirname(marker_path)
        try:
            if directory:
                os.makedirs(directory, exist_ok=True)
            fd = os.open(marker_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        except FileExistsError:
            LOG.info("TELEGRAM_STARTUP_PROBE_ALREADY_ATTEMPTED no_duplicate=true")
            return True
        except OSError as exc:
            LOG.warning("TELEGRAM_STARTUP_PROBE_MARKER_ERROR type=%s",
                        type(exc).__name__)
            return False
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as record:
                record.write("probe-reserved")
                record.flush()
                os.fsync(record.fileno())
            ok = self.send(
                "[LBank BTCUSDT | 통신 복구 검증]\n"
                "텔레그램 전송 경로의 시험 메시지입니다.\n"
                "진입 알람이나 매매 신호가 아닙니다. 자동주문 없음."
            )
            if ok:
                LOG.info("TELEGRAM_STARTUP_PROBE_DELIVERED telegram_api_ok=true "
                         "not_a_trade_alert=true")
                return True
            # After a definitively rejected call, a later restart can re-check.
            # Network ambiguity may deliver but miss acknowledgement; a retry
            # can duplicate only this non-trade diagnostic, never a trade alert.
            LOG.warning("TELEGRAM_STARTUP_PROBE_FAILED class=%s",
                        self.last_class)
            try:
                os.unlink(marker_path)
            except OSError:
                pass
            return False
        except OSError as exc:
            LOG.warning("TELEGRAM_STARTUP_PROBE_IO_ERROR type=%s",
                        type(exc).__name__)
            return False

    def health(self):
        """API-only credential/chat verification; no Telegram notification sent."""
        token_ok = self._post("getMe") is not None
        chat_ok = token_ok and bool(self.chat_id) and (
            self._post("getChat", {"chat_id": self.chat_id}) is not None)
        LOG.info("TELEGRAM_HEALTH api_token_ok=%s chat_access_ok=%s "
                 "not_a_delivery_test=true",
                 token_ok, chat_ok)
        return token_ok, chat_ok
