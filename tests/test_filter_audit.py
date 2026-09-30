from filter_audit import filter_audit


def test_pairs_missed_rallies_and_blocked_declines_without_counting_rechecks_twice():
    records = [
        {
            "signal_id": "winner",
            "decision": "rejected",
            "reasons": ["거래량 부족(0.5)"],
        },
        {
            "signal_id": "winner",
            "decision": "survival_rejected",
            "reasons": ["거래량 부족(0.8)"],
        },
        {"signal_id": "loser", "decision": "rejected", "reasons": ["거래량 부족(0.2)"]},
        {"signal_id": "pending", "decision": "rejected", "reasons": ["거래량 부족"]},
        {
            "signal_id": "watch",
            "decision": "leader_recheck_scheduled",
            "reasons": ["관찰"],
        },
    ]
    outcomes = [
        {"signal_id": "winner", "result": "target_first"},
        {"signal_id": "loser", "result": "stop_first"},
        {"signal_id": "pending", "result": "pending"},
        {"signal_id": "watch", "result": "target_first"},
        {"signal_id": "winner", "result": "target_first", "approved_candidate": True},
    ]
    audit = filter_audit(outcomes, records)
    assert audit["paired_unapproved_signals"] == 2
    assert audit["by_reason"] == [
        {
            "reason": "거래량 부족",
            "missed_target_first": 1,
            "blocked_stop_first": 1,
            "expired": 0,
        }
    ]
    assert not audit["automatic_threshold_tuning"]


def test_does_not_join_later_signal_or_future_decision_to_a_past_winner():
    outcomes = [
        {
            "signal_id": "old",
            "market": "KRW-A",
            "result": "target_first",
            "completed_at_utc": "2026-10-01T01:00:00+00:00",
        }
    ]
    records = [
        {
            "signal_id": "new",
            "market": "KRW-A",
            "decision": "rejected",
            "reasons": ["새 주기"],
        },
        {
            "signal_id": "old",
            "decision": "rejected",
            "reasons": ["나중 판단"],
            "time_utc": "2026-10-01T02:00:00+00:00",
        },
    ]
    assert filter_audit(outcomes, records)["paired_unapproved_signals"] == 0


def test_retains_paired_reasons_after_screening_ring_buffer_rolls_over():
    audit = filter_audit(
        [
            {
                "signal_id": "old",
                "result": "expired",
                "last_rejections": ["손익비 부족(1.2)"],
            }
        ],
        [],
    )
    assert audit["by_reason"][0]["expired"] == 1
    assert audit["by_reason"][0]["missed_target_first"] == 0
