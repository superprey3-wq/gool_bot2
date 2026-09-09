from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import gool_bot2.brain_in_game as menu


def _write_brain(path: Path, *, strategy: str = "another_goal", minute: int = 62, score=(0, 0)) -> None:
    now = datetime.now(timezone.utc).isoformat()
    row = {
        "signal_key": f"m1:{strategy}",
        "created_at": now,
        "telegram_sent_at": now,
        "telegram_sent": True,
        "match_id": "m1",
        "home": "Aurora",
        "away": "Real Potosi",
        "minute": minute,
        "score": list(score),
        "strategy": strategy,
        "head": strategy,
        "market": "ТБ 0.5",
        "odd": 0.0,
        "confidence_score": 92.0,
        "signal_source": "GOOL_BRAIN",
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"version": 1, "signals": {row["signal_key"]: row}}), "utf-8")


def _analysis(path: Path, *, minute: int, score=(0, 0), finished: bool = False) -> None:
    row = {
        "match_id": "m1",
        "captured_at": datetime.now(timezone.utc).isoformat(),
        "minute": minute,
        "score": list(score),
        "is_finished": finished,
    }
    path.write_text(json.dumps(row) + "\n", "utf-8")


def _no_fresh(monkeypatch) -> None:
    monkeypatch.setattr(menu, "_fresh_flashscore_states", lambda _ids: {})


def test_sent_brain_signal_appears_in_game_without_fake_bet(tmp_path, monkeypatch):
    brain = tmp_path / "brain.json"
    analysis = tmp_path / "analysis.jsonl"
    journal = tmp_path / "journal.json"
    _write_brain(brain)
    _analysis(analysis, minute=64, score=(0, 0))
    monkeypatch.setenv("GOOL_BRAIN_SIGNAL_STATE_PATH", str(brain))
    monkeypatch.setattr(menu, "_ORIGINAL_IN_GAME", lambda *_: ["EMPTY"])
    _no_fresh(monkeypatch)

    rows = menu.in_game_with_brain(journal, analysis)
    text = "\n".join(rows)

    assert "GOOL MULTI · В ИГРЕ" in text
    assert "Aurora — Real Potosi" in text
    assert "92/100 · PASS" in text
    assert "кэф —" in text
    assert not journal.exists()


def test_brain_next_goal_disappears_after_goal(tmp_path, monkeypatch):
    brain = tmp_path / "brain.json"
    analysis = tmp_path / "analysis.jsonl"
    journal = tmp_path / "journal.json"
    _write_brain(brain, minute=62, score=(0, 0))
    _analysis(analysis, minute=67, score=(1, 0))
    monkeypatch.setenv("GOOL_BRAIN_SIGNAL_STATE_PATH", str(brain))
    monkeypatch.setattr(menu, "_ORIGINAL_IN_GAME", lambda *_: ["NO OPEN SIGNALS"])
    _no_fresh(monkeypatch)

    assert menu.in_game_with_brain(journal, analysis) == ["NO OPEN SIGNALS"]


def test_first_half_brain_signal_disappears_after_halftime(tmp_path, monkeypatch):
    brain = tmp_path / "brain.json"
    analysis = tmp_path / "analysis.jsonl"
    journal = tmp_path / "journal.json"
    _write_brain(brain, strategy="goal_before_ht", minute=34, score=(0, 0))
    _analysis(analysis, minute=46, score=(0, 0))
    monkeypatch.setenv("GOOL_BRAIN_SIGNAL_STATE_PATH", str(brain))
    monkeypatch.setattr(menu, "_ORIGINAL_IN_GAME", lambda *_: ["NO OPEN SIGNALS"])
    _no_fresh(monkeypatch)

    assert menu.in_game_with_brain(journal, analysis) == ["NO OPEN SIGNALS"]


def test_finished_match_disappears_even_when_analysis_is_stale_live(tmp_path, monkeypatch):
    brain = tmp_path / "brain.json"
    analysis = tmp_path / "analysis.jsonl"
    journal = tmp_path / "journal.json"
    _write_brain(brain, minute=62, score=(0, 0))
    # Analysis stopped while the match was still live.
    _analysis(analysis, minute=74, score=(0, 0), finished=False)
    monkeypatch.setenv("GOOL_BRAIN_SIGNAL_STATE_PATH", str(brain))
    monkeypatch.setattr(menu, "_ORIGINAL_IN_GAME", lambda *_: ["NO OPEN SIGNALS"])
    monkeypatch.setattr(
        menu,
        "_fresh_flashscore_states",
        lambda ids: {
            "m1": {
                "event_id": "m1",
                "coarse_status": "3",
                "is_finished": True,
                "home_score": 0,
                "away_score": 0,
            }
        },
    )

    assert menu.in_game_with_brain(journal, analysis) == ["NO OPEN SIGNALS"]


def test_fresh_flashscore_goal_closes_brain_signal_before_analysis_catches_up(tmp_path, monkeypatch):
    brain = tmp_path / "brain.json"
    analysis = tmp_path / "analysis.jsonl"
    journal = tmp_path / "journal.json"
    _write_brain(brain, minute=62, score=(0, 0))
    _analysis(analysis, minute=63, score=(0, 0), finished=False)
    monkeypatch.setenv("GOOL_BRAIN_SIGNAL_STATE_PATH", str(brain))
    monkeypatch.setattr(menu, "_ORIGINAL_IN_GAME", lambda *_: ["NO OPEN SIGNALS"])
    monkeypatch.setattr(
        menu,
        "_fresh_flashscore_states",
        lambda ids: {
            "m1": {
                "event_id": "m1",
                "coarse_status": "2",
                "is_finished": False,
                "home_score": 1,
                "away_score": 0,
            }
        },
    )

    assert menu.in_game_with_brain(journal, analysis) == ["NO OPEN SIGNALS"]


def test_fresh_halftime_status_closes_first_half_signal(tmp_path, monkeypatch):
    brain = tmp_path / "brain.json"
    analysis = tmp_path / "analysis.jsonl"
    journal = tmp_path / "journal.json"
    _write_brain(brain, strategy="goal_before_ht", minute=34, score=(0, 0))
    _analysis(analysis, minute=35, score=(0, 0), finished=False)
    monkeypatch.setenv("GOOL_BRAIN_SIGNAL_STATE_PATH", str(brain))
    monkeypatch.setattr(menu, "_ORIGINAL_IN_GAME", lambda *_: ["NO OPEN SIGNALS"])
    monkeypatch.setattr(
        menu,
        "_fresh_flashscore_states",
        lambda ids: {
            "m1": {
                "event_id": "m1",
                "coarse_status": "2",
                "status_code": "38",
                "is_finished": False,
                "home_score": 0,
                "away_score": 0,
            }
        },
    )

    assert menu.in_game_with_brain(journal, analysis) == ["NO OPEN SIGNALS"]
