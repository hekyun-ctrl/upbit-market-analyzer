import concurrent.futures
import pytest
import monitor
from audit_store import AuditStore, configured_audit_path


def test_persistent_events_survive_reopen_and_duplicate_writes(tmp_path):
    path = str(tmp_path / "audit.sqlite3")
    first = AuditStore(path)
    row = {"market": "KRW-SUI", "decision": "rejected", "time_utc": "2026-10-04T14:00:00+00:00"}
    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
        list(pool.map(lambda _: first.append("screening_records", row), range(12)))
    second = AuditStore(path)
    assert second.read("screening_records") == [row]
    assert second.started_at == first.started_at


def test_storage_path_only_claims_persistence_when_configured(monkeypatch):
    monkeypatch.delenv("CANDIDATE_AUDIT_DB_PATH", raising=False)
    monkeypatch.delenv("RAILWAY_VOLUME_MOUNT_PATH", raising=False)
    assert configured_audit_path() is None
    monkeypatch.setenv("RAILWAY_VOLUME_MOUNT_PATH", "/data")
    assert configured_audit_path() == "/data/candidate-audit.sqlite3"


def test_report_counts_pending_and_restart_interruption_honestly(monkeypatch, tmp_path):
    monkeypatch.setenv("CANDIDATE_AUDIT_DB_PATH", str(tmp_path / "audit.sqlite3"))
    first = monitor.MonitorState()
    event = {"event": "started", "lifecycle_id": "unique", "market": "KRW-SUI",
             "signal_time_utc": "2026-10-04T14:40:00+00:00", "time_utc": "2026-10-04T14:40:00+00:00"}
    first.add_early_watch_event(event)
    pending = first.daily_performance("2026-10-04")
    assert pending["early_watch_pending"] == 1 and pending["early_watch_completed"] == 0
    assert "판정 가능한 완료 표본 없음" in monitor._daily_performance_text(pending)
    second = monitor.MonitorState()
    report = second.daily_performance("2026-10-04")
    assert not report["restart_scoped"]
    assert report["early_watch_started"] == 1 and report["early_watch_pending"] == 0
    assert report["early_watch_interrupted"] == 1 and report["target_rate_pct"] is None
    assert monitor.MonitorState().daily_performance("2026-10-04")["early_watch_interrupted"] == 1


def test_after_midnight_outcome_belongs_to_initial_detection_day(monkeypatch, tmp_path):
    monkeypatch.setenv("CANDIDATE_AUDIT_DB_PATH", str(tmp_path / "audit.sqlite3"))
    state = monitor.MonitorState()
    base = {"lifecycle_id": "winner", "market": "KRW-SUI", "signal_time_utc": "2026-10-04T14:40:00+00:00"}
    state.add_early_watch_event({**base, "event": "started", "time_utc": base["signal_time_utc"]})
    state.add_early_watch_event({**base, "event": "outcome", "time_utc": "2026-10-04T16:40:00+00:00",
        "first_touch_result": "target_first", "candidate_approved": False})
    restored = monitor.MonitorState()
    report = restored.daily_performance("2026-10-04")
    assert report["early_watch_missed_target_first"] == 1
    assert report["early_watch_completed"] == 1 and report["early_watch_interrupted"] == 0
    assert restored.daily_performance("2026-10-05")["early_watch_started"] == 0
    assert restored.candidate_performance()["early_watch_performance"]["missed_target_first"] == 1


def test_storage_failure_is_reported_as_partial_not_persistent(monkeypatch, tmp_path):
    monkeypatch.setenv("CANDIDATE_AUDIT_DB_PATH", str(tmp_path))
    state = monitor.MonitorState()
    report = state.daily_performance("2026-10-04")
    assert report["restart_scoped"] and report["audit_storage_error"]


def test_dispatch_restores_daily_count_cooldown_and_report_stamp(monkeypatch, tmp_path):
    monkeypatch.setenv("CANDIDATE_AUDIT_DB_PATH", str(tmp_path / "audit.sqlite3"))
    state = monitor.MonitorState()
    day = monitor.AlertDispatcher._today_kst()
    stamp = monitor.datetime.now(monitor.timezone.utc).isoformat()
    state.add_screening_record({"decision": "accepted", "market": "KRW-SUI", "time_utc": stamp})
    state.audit_store.set("last_daily_performance_day", day)
    monkeypatch.setattr(monitor, "MONITOR_STATE", monitor.MonitorState())
    dispatcher = monitor.AlertDispatcher()
    assert dispatcher._candidate_delivery_count == 1
    assert dispatcher._last_daily_performance_day == day
