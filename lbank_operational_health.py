"""Operational failures are not market rejections. No strategy/order logic here."""
import logging
import os
import time

LOG = logging.getLogger("lbank_operational_health")
RESERVE_BYTES = 64 * 1024 * 1024


class StorageReserve:
    """Pause optional research before it consumes the live recorder's reserve.

    Does not delete evidence or pause risk notices/entry rules. The reserve is
    an early warning, not a filesystem quota or a guarantee against disk-full.
    """
    def __init__(self, path, reserve_bytes=RESERVE_BYTES):
        self.path = os.path.abspath(path)
        self.reserve_bytes = reserve_bytes
        self.next_check = float("-inf")
        self.snapshot = None

    def check(self, now=None):
        now = time.monotonic() if now is None else now
        if self.snapshot is not None and now < self.next_check:
            return self.snapshot
        path = self.path
        while not os.path.exists(path) and path != os.path.dirname(path):
            path = os.path.dirname(path)
        try:
            stat = os.statvfs(path)
            free = stat.f_bavail * stat.f_frsize
            result = {"status": "ok" if free >= self.reserve_bytes else "low",
                      "free_bytes": free, "reserve_bytes": self.reserve_bytes,
                      "research_writable": free >= self.reserve_bytes}
        except OSError as exc:
            result = {"status": "unknown", "free_bytes": None,
                      "reserve_bytes": self.reserve_bytes,
                      "research_writable": False, "error": type(exc).__name__}
        if self.snapshot is None or result["status"] != self.snapshot["status"]:
            LOG.warning("STORAGE_RESERVE status=%s free_bytes=%s reserve_bytes=%d research_writable=%s",
                        result["status"], result["free_bytes"], self.reserve_bytes,
                        result["research_writable"])
        self.snapshot, self.next_check = result, now + 60
        return result


class JournalRecovery:
    """Retry a failed startup without discarding an existing journal or queue."""
    def __init__(self, factory, retry_seconds=60):
        self.factory, self.retry_seconds = factory, retry_seconds
        self.book = None
        self.next_attempt = float("-inf")
        self.failures = 0

    def get(self, now):
        if self.book is not None or now < self.next_attempt:
            return self.book
        self.next_attempt = now + self.retry_seconds
        try:
            self.book = self.factory()
        except Exception as exc:
            self.failures += 1
            LOG.error("ENTRY_JOURNAL_OPEN_FAILED exception=%s retry_seconds=%s",
                      type(exc).__name__, self.retry_seconds)
        else:
            LOG.info("ENTRY_JOURNAL_READY recovered=%s", bool(self.failures))
        return self.book


def blocked_diagnostic(now, audit_ready, native_ready, native):
    """Replace stale strategy diagnostics when an infrastructure gate blocks."""
    if not audit_ready:
        return {"reason": "entry_journal_unavailable", "at": now,
                "strategy_evaluated": False, "native": native}
    if not native_ready:
        return {"reason": "native_data_unavailable", "at": now,
                "strategy_evaluated": False, "native": native}
    return None


class OperationalNotices:
    """Notify persistent failures and acknowledged recoveries, never heartbeat.

    In-memory deduplication still works when the shared disk cannot be written.
    A process restart starts a new observation window. Delivery is provider ACK,
    not proof the recipient read the message.
    """
    def __init__(self, sender, delay=120, retry_seconds=300):
        self.sender, self.delay, self.retry_seconds = sender, delay, retry_seconds
        self.state = None
        self.since = 0
        self.announced = None
        self.next_attempt = float("-inf")

    def observe(self, now, issue):
        if issue != self.state:
            self.state, self.since = issue, now
            self.next_attempt = now
            LOG.info("ENTRY_OPERATIONAL_STATE issue=%s strategy_rejection=false", issue or "none")
        if issue == self.announced or now < self.next_attempt:
            return
        # Healthy startup and short reconnects create no messages. Recovery is
        # also debounced to avoid alternating notices during an unstable feed.
        if now - self.since < self.delay:
            return
        descriptions = {
            "journal": "장부 저장 연결 문제로 신규 진입 판단을 보류하고 있습니다.",
            "native": "완료 봉 자료가 부족하거나 끊겨 신규 진입 판단을 보류하고 있습니다.",
            "storage_low": "저장 여유 공간이 부족해 추가 연구 기록을 일시 중단했습니다. 진입 조건은 변경하지 않았습니다.",
            "storage_unknown": "저장 공간 상태를 확인하지 못해 추가 연구 기록을 일시 중단했습니다.",
            "monitor_error": "시세 감시 처리 오류가 지속되고 있습니다. 알람이 없다는 사실을 진입 조건 없음으로 해석할 수 없습니다.",
            None: "봉 수신·장부 연결·저장 공간의 장애 보류 사유가 해소됐습니다. 진입 알림은 기존 조건 충족 시 발송됩니다.",
        }
        message = ("[LBank BTCUSDT | 운영 상태]\n" + descriptions[issue]
                   + "\n진입 신호가 아닙니다. 실제 주문을 실행하지 않습니다.")
        self.next_attempt = now + self.retry_seconds
        try:
            acknowledged = bool(self.sender(message))
        except Exception:
            acknowledged = False
        if acknowledged:
            self.announced = issue
        LOG.info("ENTRY_OPERATIONAL_NOTICE issue=%s api_ack=%s", issue or "recovered", acknowledged)
