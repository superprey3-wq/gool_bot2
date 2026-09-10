from __future__ import annotations

import inspect
from datetime import datetime, timedelta, timezone

import gool_bot2.multi_product as product
import gool_bot2.production_journal_serialization as serialization
import gool_bot2.result_delivery_guard as delivery_guard
import gool_bot2.result_delivery_once as delivery_once
import gool_bot2.stale_replay_guard as stale_guard
from gool_bot2.journal import load_signal_journal, save_signal_journal
from gool_bot2.orphan_pending_reconcile import reconcile_orphaned_pending
from gool_bot2.production_journal_repair import repair_public_journal


def _brain_pending(**extra):
    row = {
        "created_at": datetime.now(timezone.utc).isoformat(),
        "mode": "active",
        "entry_key": "brain:m1:another_goal",
        "signal_key": "m1:another_goal",
        "match_id": "m1",
        "home": "Home",
        "away": "Away",
        "minute": 60,
        "score": [1, 0],
        "strategy": "another_goal",
        "market": "ТБ 1.5",
        "market_family": "match_total",
        "market_key": "match_total:1.5",
        "odd": None,
        "source": "brain_primary:another_goal",
        "signal_source": "GOOL_BRAIN",
        "tracking_only": True,
        "bank_tracking": False,
        "result": "pending",
        "telegram_sent": True,
    }
    row.update(extra)
    return row


def test_product_pipeline_has_one_brain_journal_owner_and_one_result_sender():
    product_source = inspect.getsource(product.install_multi_product)
    guard_source = inspect.getsource(delivery_guard.install_result_delivery_guard)
    stale_source = inspect.getsource(stale_guard.install_stale_replay_guard)

    assert "install_brain_journal_tracking" in product_source
    assert "install_brain_journal_results" not in product_source
    assert "install_production_journal_serialization" in product_source
    assert "multi_telegram.emit_multi_results = _guarded_emit" in guard_source
    assert "multi_runtime.emit_multi_results = _guarded_emit" in guard_source
    assert "brain_journal_results" not in stale_source


def test_new_active_market_entry_is_not_public_before_telegram(monkeypatch):
    monkeypatch.setitem(
        serialization._ORIGINALS,
        "entry_from_decision",
        lambda *args, **kwargs: {"mode": "active", "entry_key": "steam:m1", "result": "pending"},
    )

    row = serialization._entry_from_decision(None, None, {}, data_quality=0.8)

    assert row is not None
    assert row["telegram_sent"] is False


def test_startup_repair_collapses_dual_brain_rows_to_one_pending(tmp_path):
    path = tmp_path / "journal.json"
    canonical = _brain_pending()
    legacy = {
        **canonical,
        "tracking_only": False,
        "accounting_mode": "result_only",
        "non_monetary": True,
        "result": "tracking",
        "odd": 0.0,
        "virtual_stake_rub": 0.0,
    }
    save_signal_journal(path, [canonical, legacy])

    result = repair_public_journal(path)
    rows = load_signal_journal(path)

    assert result["duplicates_removed"] == 1
    assert len(rows) == 1
    row = rows[0]
    assert row["result"] == "pending"
    assert row["tracking_only"] is True
    assert row["bank_tracking"] is False
    assert row["odd"] is None
    assert "accounting_mode" not in row
    assert "virtual_stake_rub" not in row


def test_startup_repair_preserves_delivered_result_and_blocks_replay(tmp_path):
    path = tmp_path / "journal.json"
    pending = _brain_pending()
    delivered = {
        **pending,
        "result": "won",
        "settled_at": datetime.now(timezone.utc).isoformat(),
        "settled_minute": 67,
        "settled_score": [1, 1],
        "result_notification_pending": True,
        "result_telegram_sent": True,
        "result_telegram_sent_at": datetime.now(timezone.utc).isoformat(),
        "result_telegram_delivery_count": 1,
        "accounting_mode": "result_only",
    }
    save_signal_journal(path, [pending, delivered])

    repair_public_journal(path)
    rows = load_signal_journal(path)

    assert len(rows) == 1
    row = rows[0]
    assert row["result"] == "won"
    assert row["result_telegram_sent"] is True
    assert row["result_notification_pending"] is False
    assert delivery_once.reserve_result_delivery(path, row) is None


def test_old_orphan_with_timeline_is_settled_without_late_result_card(tmp_path, monkeypatch):
    path = tmp_path / "journal.json"
    old = (datetime.now(timezone.utc) - timedelta(hours=5)).isoformat()
    save_signal_journal(
        path,
        [
            {
                "created_at": old,
                "mode": "active",
                "entry_key": "steam:m2:0-0:match_total:0.5",
                "match_id": "m2",
                "home": "Home",
                "away": "Away",
                "minute": 55,
                "score": [0, 0],
                "strategy": "steam_another_goal",
                "market": "ТБ 0.5",
                "market_family": "match_total",
                "market_key": "match_total:0.5",
                "odd": 1.8,
                "result": "pending",
                "telegram_sent": True,
            }
        ],
    )
    monkeypatch.setenv("GOOL_ORPHAN_PENDING_CLOSE_HOURS", "4")

    class FakeFlashscore:
        def event_states(self, ids):
            return {}

        def fetch_goal_timeline(self, match_id):
            return [
                {
                    "minute": 70,
                    "period": "2H",
                    "event_type": "goal",
                    "score": [1, 0],
                }
            ]

    monkeypatch.setattr("gool_bot2.providers.flashscore.FlashscoreProvider", FakeFlashscore)

    assert reconcile_orphaned_pending(path) == 1
    row = load_signal_journal(path)[0]
    assert row["result"] == "won"
    assert row["settled_score"] == [1, 0]
    assert row["result_notification_pending"] is False
    assert row["result_notification_suppressed"] is True
    assert row["result_notification_suppression_reason"] == "orphan_historical_no_replay"


def test_old_orphan_without_reliable_timeline_becomes_void_not_fake_loss(tmp_path, monkeypatch):
    path = tmp_path / "journal.json"
    old = (datetime.now(timezone.utc) - timedelta(hours=5)).isoformat()
    save_signal_journal(path, [_brain_pending(created_at=old, match_id="m3", entry_key="brain:m3:another_goal")])
    monkeypatch.setenv("GOOL_ORPHAN_PENDING_CLOSE_HOURS", "4")

    class FakeFlashscore:
        def event_states(self, ids):
            return {}

        def fetch_goal_timeline(self, match_id):
            return []

    monkeypatch.setattr("gool_bot2.providers.flashscore.FlashscoreProvider", FakeFlashscore)

    assert reconcile_orphaned_pending(path) == 1
    row = load_signal_journal(path)[0]
    assert row["result"] == "void"
    assert row["result_notification_suppressed"] is True
    assert row["result_notification_pending"] is False
