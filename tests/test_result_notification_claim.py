from __future__ import annotations

from datetime import datetime, timedelta, timezone

from gool_bot2.journal import load_signal_journal, save_signal_journal
from gool_bot2.multi_delivery import finalize_result_delivery, pending_result_notifications


def _row() -> dict:
    return {
        "entry_key": "m1:0-0:match_total:0.5",
        "match_id": "m1",
        "mode": "active",
        "telegram_sent": True,
        "result": "won",
        "settled_at": "2026-09-10T07:00:00+00:00",
        "settled_minute": 61,
        "settled_score": [1, 0],
        "result_notification_pending": True,
        "result_notification_created_at": "2026-09-10T07:00:01+00:00",
    }


def test_pending_result_is_claimed_only_once_before_delivery(tmp_path):
    path = tmp_path / "journal.json"
    save_signal_journal(path, [_row()])

    first = pending_result_notifications(path, match_id="m1")
    second = pending_result_notifications(path, match_id="m1")

    assert len(first) == 1
    assert first[0]["result_notification_claim_id"]
    assert second == []

    assert finalize_result_delivery(path, first[0], 1) is True
    assert pending_result_notifications(path, match_id="m1") == []

    stored = load_signal_journal(path)[0]
    assert stored["result_notification_pending"] is False
    assert stored["result_telegram_sent"] is True
    assert "result_notification_claim_id" not in stored


def test_expired_claim_can_retry_after_failed_or_crashed_delivery(tmp_path):
    path = tmp_path / "journal.json"
    row = _row()
    row.update(
        {
            "result_notification_claim_id": "old-claim",
            "result_notification_claimed_at": (datetime.now(timezone.utc) - timedelta(minutes=10)).isoformat(),
            "result_notification_claim_version": row["result_notification_created_at"],
        }
    )
    save_signal_journal(path, [row])

    retried = pending_result_notifications(path, match_id="m1")

    assert len(retried) == 1
    assert retried[0]["result_notification_claim_id"] != "old-claim"


def test_new_settlement_version_bypasses_an_old_inflight_claim(tmp_path):
    path = tmp_path / "journal.json"
    row = _row()
    row.update(
        {
            "result_notification_claim_id": "claim-for-old-result",
            "result_notification_claimed_at": datetime.now(timezone.utc).isoformat(),
            "result_notification_claim_version": "2026-09-10T06:59:00+00:00",
        }
    )
    save_signal_journal(path, [row])

    corrected = pending_result_notifications(path, match_id="m1")

    assert len(corrected) == 1
    assert corrected[0]["result_notification_claim_id"] != "claim-for-old-result"
    assert corrected[0]["result_notification_claim_version"] == row["result_notification_created_at"]
