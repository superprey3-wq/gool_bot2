from __future__ import annotations

from datetime import datetime, timezone

import gool_bot2.journal_in_game as in_game
import gool_bot2.journal_report as report
from gool_bot2.journal import save_signal_journal


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
