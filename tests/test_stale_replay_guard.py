from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import gool_bot2.brain_card_restore as card
import gool_bot2.stale_replay_guard as guard
from gool_bot2.journal import load_signal_journal, save_signal_journal


def _entry() -> dict:
    return {"match_id": "m1", "score": [0, 0], "strategy": "another_goal"}


def test_brain_signal_is_dropped_when_fresh_event_state_is_missing(monkeypatch):
    monkeypatch.setattr(card, "_fresh_flashscore_state", lambda match_id: None)
    reason = guard._strict_pre_send_reason(
        {"match": {"flashscore_event_id": "m1", "home_score": 0, "away_score": 0}},
        SimpleNamespace(score=(0, 0)),
        _entry(),
    )
    assert reason == "fresh_flashscore_state_unavailable"


def test_brain_signal_is_dropped_when_event_is_no_longer_live(monkeypatch):
    monkeypatch.setattr(
        card,
        "_fresh_flashscore_state",
        lambda match_id: {
            "event_id": match_id,
            "coarse_status": "3",
            "is_live": False,
            "is_finished": True,
            "home_score": 1,
            "away_score": 0,
        },
    )
    reason = guard._strict_pre_send_reason(
        {"match": {"flashscore_event_id": "m1", "home_score": 0, "away_score": 0}},
        SimpleNamespace(score=(0, 0)),
        _entry(),
    )
    assert reason == "match_finished"


def test_old_result_notification_is_suppressed_but_fresh_one_remains(tmp_path, monkeypatch):
    path = tmp_path / "journal.json"
    now = datetime.now(timezone.utc)
    old = {
        "entry_key": "old",
        "match_id": "old",
        "created_at": (now - timedelta(hours=8)).isoformat(),
        "result": "won",
        "result_notification_pending": True,
    }
    fresh = {
        "entry_key": "fresh",
        "match_id": "fresh",
        "created_at": (now - timedelta(minutes=10)).isoformat(),
        "result": "won",
        "result_notification_pending": True,
    }
    save_signal_journal(path, [old, fresh])
    monkeypatch.setenv("GOOL_RESULT_REPLAY_MAX_SIGNAL_AGE_HOURS", "4")

    changed = guard._suppress_stale_result_replays(path)
    rows = {row["entry_key"]: row for row in load_signal_journal(path)}

    assert changed == 1
    assert rows["old"]["result_notification_pending"] is False
    assert rows["old"]["result_notification_suppressed"] is True
    assert rows["old"]["result_notification_suppression_reason"] == "signal_age_gt_4h"
    assert rows["fresh"]["result_notification_pending"] is True


def test_explicit_historical_backfill_notification_is_never_replayed(tmp_path):
    path = tmp_path / "journal.json"
    row = {
        "entry_key": "backfill",
        "match_id": "m1",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "result": "lost",
        "result_notification_pending": True,
        "result_notification_suppressed": True,
    }
    save_signal_journal(path, [row])

    assert guard._suppress_stale_result_replays(path) == 1
    stored = load_signal_journal(path)[0]
    assert stored["result_notification_pending"] is False
    assert stored["result_notification_suppression_reason"] == "historical_backfill"
