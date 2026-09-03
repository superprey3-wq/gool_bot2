import json
from datetime import datetime, timezone
from pathlib import Path

from gool_bot2 import bot_menu, telegram, telegram_in_game_guard


def _write_json(path: Path, rows):
    path.write_text(json.dumps(rows), encoding="utf-8")


def _write_analysis(path: Path, rows):
    with path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row) + "\n")


def test_in_game_menu_hides_finished_pending(monkeypatch, tmp_path: Path):
    now = datetime.now(timezone.utc).isoformat()
    journal = tmp_path / "journal.json"
    analysis = tmp_path / "analysis.jsonl"
    _write_json(journal, [
        {"match_id": "LIVE0001", "head": "another_goal", "minute": 68, "score": [0, 1], "result": "pending", "created_at": now, "home": "Live A", "away": "Live B", "probability": 0.66},
        {"match_id": "DONE0001", "head": "two_more_goals", "minute": 64, "score": [1, 1], "result": "pending", "created_at": now, "home": "Done A", "away": "Done B", "probability": 0.50},
    ])
    _write_analysis(analysis, [
        {"captured_at": now, "match_id": "LIVE0001", "head": "analysis_scope", "minute": 70, "score": [0, 1]},
        {"captured_at": now, "match_id": "DONE0001", "head": "analysis_scope", "minute": 90, "score": [1, 2]},
    ])

    class FakeProvider:
        def event_states(self, ids):
            return {
                "LIVE0001": {"is_live": True, "is_finished": False},
                "DONE0001": {"is_live": False, "is_finished": True},
            }

    monkeypatch.setattr(telegram_in_game_guard, "FlashscoreProvider", FakeProvider)
    sections = telegram_in_game_guard._guarded_in_game_sections(journal, analysis)
    text = "\n".join(sections)
    assert "Live A" in text
    assert "Done A" not in text
    assert "Активных сигналов: <b>1</b>" in text
