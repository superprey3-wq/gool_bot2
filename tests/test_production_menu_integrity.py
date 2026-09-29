from __future__ import annotations

from datetime import datetime, timedelta, timezone

import gool_bot2.journal_in_game as in_game
import gool_bot2.journal_report as report
from gool_bot2.journal import load_signal_journal, save_signal_journal
from gool_bot2.production_journal_repair import repair_public_journal


def _row(key: str, *, result: str = "pending", sent: bool = True, home: str = "Home") -> dict:
    return {
        "created_at": datetime.now(timezone.utc).isoformat(),
        "mode": "active",
        "entry_key": key,
        "match_id": key,
        "home": home,
        "away": "Away",
        "minute": 60,
        "score": [1, 0],
        "strategy": "another_goal",
        "market": "ТБ 1.5",
        "market_family": "match_total",
        "market_key": "match_total:1.5",
        "odd": None,
        "rating": 80,
        "signal_source": "GOOL_BRAIN",
        "source": "brain_primary:another_goal",
        "tracking_only": True,
        "result": result,
        "profit_units": None,
        "telegram_sent": sent,
    }


def test_production_in_game_ignores_legacy_responder_journal_path(tmp_path, monkeypatch):
    canonical = tmp_path / "gool_multi_journal.json"
    legacy = tmp_path / "signal_journal.json"
    analysis = tmp_path / "gool_multi_analysis.jsonl"
    legacy_analysis = tmp_path / "legacy_analysis.jsonl"

    save_signal_journal(canonical, [_row("canonical", home="Canonical Bet")])
    save_signal_journal(legacy, [_row("legacy", home="Wrong Legacy Bet")])
    analysis.write_text("", "utf-8")
    legacy_analysis.write_text("", "utf-8")

    monkeypatch.setenv("GOOL_MULTI_JOURNAL_PATH", str(canonical))
    monkeypatch.setenv("GOOL_MULTI_ANALYSIS_PATH", str(analysis))

    text = "\n".join(in_game.production_in_game_sections(legacy, legacy_analysis))

    assert "Canonical Bet" in text
    assert "Wrong Legacy Bet" not in text
    assert "Открыто: <b>1</b>" in text


def test_production_report_is_read_only_and_excludes_unsent_rows(tmp_path, monkeypatch):
    canonical = tmp_path / "gool_multi_journal.json"
    sent = _row("sent", result="won", sent=True, home="Sent")
    sent["settled_at"] = datetime.now(timezone.utc).isoformat()
    unsent = _row("unsent", result="lost", sent=False, home="Unsent")
    unsent["settled_at"] = datetime.now(timezone.utc).isoformat()
    save_signal_journal(canonical, [sent, unsent])

    monkeypatch.setenv("GOOL_MULTI_JOURNAL_PATH", str(canonical))
    monkeypatch.setenv("REPORT_TIMEZONE", "UTC")
    monkeypatch.setattr(
        "gool_bot2.multi_menu.reconcile_pending",
        lambda: (_ for _ in ()).throw(AssertionError("report renderer must not reconcile")),
    )

    text = report.production_report_text(tmp_path / "legacy.json")

    assert "✅ <b>1</b> · ❌ <b>0</b>" in text
    assert "⏳ <b>0</b>" in text


def test_startup_repair_prefers_latest_settlement_not_old_delivered_result(tmp_path):
    path = tmp_path / "journal.json"
    now = datetime.now(timezone.utc)
    old_won = _row("brain:m1:another_goal", result="won")
    old_won.update(
        {
            "signal_key": "m1:another_goal",
            "match_id": "m1",
            "settled_at": (now - timedelta(minutes=2)).isoformat(),
            "settled_score": [1, 1],
            "result_notification_pending": False,
            "result_telegram_sent": True,
            "result_telegram_sent_at": (now - timedelta(minutes=2)).isoformat(),
            "result_telegram_delivery_count": 1,
        }
    )
    corrected_lost = {**old_won}
    corrected_lost.update(
        {
            "result": "lost",
            "settled_at": (now - timedelta(minutes=1)).isoformat(),
            "settled_score": [1, 0],
            "result_notification_pending": True,
            "result_telegram_sent": False,
        }
    )
    corrected_lost.pop("result_telegram_sent_at", None)
    corrected_lost.pop("result_telegram_delivery_count", None)
    save_signal_journal(path, [old_won, corrected_lost])

    repair_public_journal(path)
    repaired = load_signal_journal(path)

    assert len(repaired) == 1
    row = repaired[0]
    assert row["result"] == "lost"
    assert row["settled_score"] == [1, 0]
    assert row.get("result_telegram_sent") is not True
    assert row["result_notification_pending"] is False
    assert row["result_notification_suppressed"] is True
    assert row["result_notification_suppression_reason"] == "startup_repair_conflicting_delivered_result"


def test_public_report_reconstructs_profit_from_odds(tmp_path, monkeypatch):
    canonical = tmp_path / "gool_multi_journal.json"
    now = datetime.now(timezone.utc).isoformat()
    win = _row("w", result="won", sent=True, home="Win")
    win.update({"created_at": now, "odd": 1.65, "profit_units": None})
    loss = _row("l", result="lost", sent=True, home="Loss")
    loss.update({"created_at": now, "odd": 1.80, "profit_units": None})
    save_signal_journal(canonical, [win, loss])

    monkeypatch.setenv("GOOL_MULTI_JOURNAL_PATH", str(canonical))
    monkeypatch.setenv("REPORT_TIMEZONE", "UTC")

    text = report.production_report_text(tmp_path / "legacy.json")

    assert "P/L <b>-0.35u</b>" in text
    assert "ROI <b>-17.5%</b>" in text
    assert "ср.кэф <b>1.73</b>" in text
    assert "ИТОГО ДНЯ" in text


def test_public_report_marks_missing_winner_price_instead_of_fake_zero(tmp_path, monkeypatch):
    canonical = tmp_path / "gool_multi_journal.json"
    now = datetime.now(timezone.utc).isoformat()
    win = _row("w", result="won", sent=True, home="Old Win")
    win.update({"created_at": now, "odd": None, "profit_units": None})
    save_signal_journal(canonical, [win])

    monkeypatch.setenv("GOOL_MULTI_JOURNAL_PATH", str(canonical))
    monkeypatch.setenv("REPORT_TIMEZONE", "UTC")

    text = report.production_report_text(tmp_path / "legacy.json")

    assert "P/L <b>+0.00u*</b>" in text
    assert "P/L/ROI неполные" in text
