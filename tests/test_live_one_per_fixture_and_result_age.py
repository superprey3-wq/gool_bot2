from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from gool_bot2 import brain_primary_mode as primary
from gool_bot2.production_signal_throughput import _target_aware_brain_entry
from gool_bot2.stale_replay_guard import _suppress_stale_result_replays
from gool_bot2.journal import save_signal_journal, load_signal_journal


def test_public_football_signal_blocks_another_market_in_same_fixture(tmp_path, monkeypatch):
    path = tmp_path / "signals.json"
    path.write_text(json.dumps({"signals": {
        "fs123:goal_before_ht:goal": {
            "match_id": "fs123", "telegram_sent": True, "signal_key": "fs123:goal_before_ht:goal",
        }
    }}), "utf-8")
    monkeypatch.setattr(primary, "_signal_state_path", lambda: path)
    winner = SimpleNamespace(source="brain_primary:verified", strategy="another_goal",
                             key="over_1_5", label="Еще гол", odd=1.70,
                             model_probability=.80, rating=80.)
    record = {"match": {
        "flashscore_event_id": "fs123", "home": "A", "away": "B",
        "minute": 70, "home_score": 1, "away_score": 0,
    }}
    assert _target_aware_brain_entry(record, SimpleNamespace(winner=winner)) is None
    record["match"]["flashscore_event_id"] = "different"
    assert _target_aware_brain_entry(record, SimpleNamespace(winner=winner)) is not None


def test_fresh_prematch_settlement_can_notify_even_when_pick_is_old(tmp_path, monkeypatch):
    path = tmp_path / "journal.json"
    now = datetime.now(timezone.utc)
    rows = [
        {
            "entry_key": "fresh_after_kickoff",
            "created_at": (now - timedelta(hours=9)).isoformat(),
            "settled_at": (now - timedelta(minutes=10)).isoformat(),
            "phase": "PREMATCH", "telegram_sent": True,
            "result": "won", "result_notification_pending": True,
        },
        {
            "entry_key": "historical_old_settlement",
            "created_at": (now - timedelta(hours=10)).isoformat(),
            "settled_at": (now - timedelta(hours=8)).isoformat(),
            "phase": "PREMATCH", "telegram_sent": True,
            "result": "won", "result_notification_pending": True,
        },
    ]
    save_signal_journal(path, rows)
    monkeypatch.setenv("GOOL_RESULT_REPLAY_MAX_SIGNAL_AGE_HOURS", "4")
    assert _suppress_stale_result_replays(path) == 1
    state = {r["entry_key"]: r for r in load_signal_journal(path)}
    assert state["fresh_after_kickoff"]["result_notification_pending"]
    assert not state["historical_old_settlement"]["result_notification_pending"]
