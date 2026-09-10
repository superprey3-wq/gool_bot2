from __future__ import annotations

import json
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone

import gool_bot2.pending_reconcile_guard as pending_guard
from gool_bot2.journal import journal_transaction, load_signal_journal, save_signal_journal
from gool_bot2.production_integrity import repair_public_journal


def _old_iso(hours: float = 10.0) -> str:
    return (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat()


def _brain_row(*, result: str = "pending", old_style: bool = False) -> dict:
    row = {
        "created_at": _old_iso(1),
        "mode": "active",
        "entry_key": "brain:fs-a:another_goal",
        "signal_key": "fs-a:another_goal",
        "match_id": "fs-a",
        "home": "Home",
        "away": "Away",
        "minute": 60,
        "score": [0, 0],
        "strategy": "another_goal",
        "market": "ТБ 0.5",
        "market_key": "match_total:0.5",
        "market_family": "match_total",
        "source": "brain_primary:another_goal",
        "signal_source": "GOOL_BRAIN",
        "telegram_sent": True,
        "result": result,
    }
    if old_style:
        row["accounting_mode"] = "result_only"
        row["result"] = "tracking" if result == "pending" else result
        row["brain_signal_key"] = "fs-a:another_goal"
    else:
        row["tracking_only"] = True
    return row


def test_startup_repair_collapses_two_historical_brain_journal_rows(tmp_path):
    path = tmp_path / "gool_multi_journal.json"
    modern = _brain_row(result="pending")
    legacy = _brain_row(result="won", old_style=True)
    legacy.update(
        {
            "settled_at": _old_iso(0.5),
            "settled_minute": 64,
            "settled_score": [1, 0],
            "settlement_source": "flashscore_var_confirmed_live_score",
            "result_notification_pending": True,
            "result_telegram_sent": True,
            "result_telegram_sent_at": _old_iso(0.4),
        }
    )
    save_signal_journal(path, [modern, legacy])

    stats = repair_public_journal(path)
    rows = load_signal_journal(path)

    assert stats["before"] == 2
    assert stats["after"] == 1
    assert stats["duplicates_removed"] == 1
    assert len(rows) == 1
    row = rows[0]
    assert row["entry_key"] == "brain:fs-a:another_goal"
    assert row["brain_signal_key"] == "fs-a:another_goal"
    assert row["result"] == "won"
    assert row["settled_score"] == [1, 0]
    assert row["tracking_only"] is True
    assert row["non_monetary"] is True
    assert row["result_telegram_sent"] is True
    assert row["result_notification_pending"] is False
    assert "accounting_mode" not in row


def test_startup_repair_turns_legacy_tracking_into_one_pending_row(tmp_path):
    path = tmp_path / "gool_multi_journal.json"
    save_signal_journal(path, [_brain_row(result="pending"), _brain_row(result="pending", old_style=True)])

    repair_public_journal(path)
    rows = load_signal_journal(path)

    assert len(rows) == 1
    assert rows[0]["result"] == "pending"
    assert rows[0]["pipeline_version"] == "unified_v1"


def test_journal_transaction_prevents_live_and_menu_lost_update(tmp_path):
    path = tmp_path / "gool_multi_journal.json"
    save_signal_journal(path, [])

    def append(key: str) -> None:
        with journal_transaction(path):
            rows = load_signal_journal(path)
            # Make the old race deterministic enough: without the transaction,
            # both threads would commonly read [] before either save.
            time.sleep(0.03)
            rows.append({"entry_key": key})
            save_signal_journal(path, rows)

    with ThreadPoolExecutor(max_workers=2) as pool:
        list(pool.map(append, ["live", "menu"]))

    assert {row["entry_key"] for row in load_signal_journal(path)} == {"live", "menu"}


def test_missing_flashscore_state_eventually_voids_without_fabricating_loss(tmp_path, monkeypatch):
    path = tmp_path / "gool_multi_journal.json"
    row = {
        "created_at": _old_iso(10),
        "telegram_sent_at": _old_iso(10),
        "mode": "active",
        "entry_key": "brain:missing:another_goal",
        "match_id": "missing",
        "home": "Old Home",
        "away": "Old Away",
        "minute": 60,
        "score": [1, 0],
        "strategy": "another_goal",
        "market": "ТБ 1.5",
        "market_key": "match_total:1.5",
        "market_family": "match_total",
        "telegram_sent": True,
        "tracking_only": True,
        "result": "pending",
    }
    save_signal_journal(path, [row])
    monkeypatch.setenv("GOOL_MULTI_JOURNAL_PATH", str(path))
    monkeypatch.setenv("GOOL_PENDING_MISSING_STATE_RECHECK_HOURS", "4")
    monkeypatch.setenv("GOOL_PENDING_UNVERIFIABLE_VOID_HOURS", "8")

    class Provider:
        def event_states(self, ids):
            return {}

        def fetch_goal_timeline(self, match_id):
            return []

    monkeypatch.setattr("gool_bot2.providers.flashscore.FlashscoreProvider", Provider)

    assert pending_guard._reconcile_missing_states() == 1
    settled = load_signal_journal(path)[0]
    assert settled["result"] == "void"
    assert settled["settlement_source"] == "flashscore_state_missing_unverifiable_void"
    assert settled["result_notification_pending"] is False
    assert settled["result_notification_suppressed"] is True


def test_missing_state_with_confirmed_timeline_settles_win_not_void(tmp_path, monkeypatch):
    path = tmp_path / "gool_multi_journal.json"
    row = {
        "created_at": _old_iso(5),
        "telegram_sent_at": _old_iso(5),
        "mode": "active",
        "entry_key": "steam:timeline-win",
        "match_id": "timeline-win",
        "home": "Home",
        "away": "Away",
        "minute": 60,
        "score": [0, 0],
        "strategy": "another_goal",
        "market": "ТБ 0.5",
        "market_key": "match_total:0.5",
        "market_family": "match_total",
        "odd": 1.8,
        "telegram_sent": True,
        "result": "pending",
    }
    save_signal_journal(path, [row])
    monkeypatch.setenv("GOOL_MULTI_JOURNAL_PATH", str(path))
    monkeypatch.setenv("GOOL_PENDING_MISSING_STATE_RECHECK_HOURS", "4")
    monkeypatch.setenv("GOOL_PENDING_UNVERIFIABLE_VOID_HOURS", "8")

    class Provider:
        def event_states(self, ids):
            return {}

        def fetch_goal_timeline(self, match_id):
            return [
                {
                    "minute": 66,
                    "period": "2H",
                    "event_type": "goal",
                    "score": [1, 0],
                }
            ]

    monkeypatch.setattr("gool_bot2.providers.flashscore.FlashscoreProvider", Provider)

    assert pending_guard._reconcile_missing_states() >= 1
    settled = load_signal_journal(path)[0]
    assert settled["result"] == "won"
    assert settled["settled_score"] == [1, 0]
    assert settled["result_notification_pending"] is True
