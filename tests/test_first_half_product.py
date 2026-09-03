import json
from datetime import datetime, timezone
from pathlib import Path

from gool_bot2 import bot_menu, first_half_product, signal_cards, telegram


def test_first_half_strategy_is_first_class_in_report(tmp_path: Path):
    now = datetime.now(timezone.utc).isoformat()
    journal = tmp_path / "journal.json"
    journal.write_text(json.dumps([
        {"match_id": "FH1", "head": "goal_before_ht", "minute": 28, "score": [0, 0], "result": "won", "created_at": now},
    ]), encoding="utf-8")
    text = bot_menu.report_text(journal)
    assert "🟡 Гол до перерыва" in text
    assert "✅ 1" in text


def test_first_half_live_pressure_stays_active_after_25(monkeypatch):
    values = {
        "xg": (0.9, 0.6),
        "xgot": (0.7, 0.5),
        "shots": (7.0, 6.0),
        "shots_on_target": (3.0, 2.0),
        "big_chances": (1.0, 1.0),
        "corners": (3.0, 2.0),
        "dangerous_attacks": (30.0, 28.0),
    }
    monkeypatch.setattr(first_half_product.local_ensemble, "_pair_total", lambda record, key: sum(values[key]))
    out = first_half_product._first_half_live_analysis_40({"match": {"minute": 35, "is_halftime": False}})
    assert out["pressure_score"] is not None


def test_first_half_card_theme_and_menu(monkeypatch, tmp_path: Path):
    assert "goal_before_ht" in signal_cards.THEMES
    assert "goal_before_ht" in bot_menu.MAIN_HEADS

    now = datetime.now(timezone.utc).isoformat()
    journal = tmp_path / "journal.json"
    analysis = tmp_path / "analysis.jsonl"
    journal.write_text(json.dumps([
        {"match_id": "FH2", "head": "goal_before_ht", "minute": 30, "score": [1, 0], "result": "pending", "created_at": now, "home": "A", "away": "B", "probability": 0.72},
    ]), encoding="utf-8")
    analysis.write_text(json.dumps({"captured_at": now, "match_id": "FH2", "head": "goal_before_ht", "minute": 31, "score": [1, 0]}) + "\n", encoding="utf-8")

    class FakeProvider:
        def event_states(self, ids):
            return {"FH2": {"is_live": True, "is_finished": False}}

    monkeypatch.setattr(first_half_product, "FlashscoreProvider", FakeProvider)
    text = "\n".join(telegram.in_game_sections(journal, analysis))
    assert "ГОЛ ДО ПЕРЕРЫВА" in text
    assert "A — B" in text
