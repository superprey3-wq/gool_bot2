from __future__ import annotations

from types import SimpleNamespace

from gool_bot2.journal import save_signal_journal
from gool_bot2 import strict_in_game_live as live_menu


def _row(match_id: str, home: str, away: str, *, strategy: str = "another_goal", result: str = "pending", minute: int = 60, odd: float = 0.0):
    score = [0, 0]
    market = "1Т ТБ 0.5" if strategy == "goal_before_ht" else "ТБ 0.5"
    return {
        "entry_key": f"{match_id}:{strategy}:{minute}",
        "match_id": match_id,
        "home": home,
        "away": away,
        "strategy": strategy,
        "head": strategy,
        "result": result,
        "mode": "active",
        "created_at": f"2026-09-10T10:{minute % 60:02d}:00+00:00",
        "minute": minute,
        "score": score,
        "market": market,
        "odd": odd,
        "event_score": 88.0,
        "confidence_score": 88.0,
        "signal_source": "GOOL_BRAIN",
        "market_pressure_pp": 0.0,
        "reason": "test",
    }


def _live(match_id: str, *, minute: int, home_score: int = 0, away_score: int = 0, is_halftime: bool = False):
    return SimpleNamespace(
        provider_match_id=match_id,
        minute=minute,
        home_score=home_score,
        away_score=away_score,
        is_halftime=is_halftime,
    )


def test_in_game_shows_only_matches_flashscore_confirms_live(tmp_path, monkeypatch):
    path = tmp_path / "journal.json"
    save_signal_journal(
        path,
        [
            _row("live01", "North Korea U20 W", "Costa Rica U20 W", minute=13),
            _row("old001", "Rangers", "St. Mirren", minute=72, odd=1.46),
            _row("old002", "Moreirense", "Benfica", result="tracking", minute=10, odd=1.23),
        ],
    )
    monkeypatch.setattr("gool_bot2.multi_menu.reconcile_pending", lambda: 0)
    monkeypatch.setattr(
        live_menu.FlashscoreProvider,
        "live_matches",
        lambda self: [_live("live01", minute=37, home_score=1, away_score=0)],
    )

    text = "\n".join(live_menu.strict_in_game_sections(path))

    assert "Открыто: <b>1</b>" in text
    assert "North Korea U20 W" in text
    assert "37' · 1:0" in text
    assert "Rangers" not in text
    assert "Moreirense" not in text
    assert "@ —" in text


def test_first_half_entry_is_not_shown_after_second_half_starts(tmp_path, monkeypatch):
    path = tmp_path / "journal.json"
    save_signal_journal(
        path,
        [
            _row("same001", "France U20 W", "Ecuador U20 W", strategy="goal_before_ht", minute=23),
            _row("same001", "France U20 W", "Ecuador U20 W", strategy="another_goal", minute=60, odd=1.36),
        ],
    )
    monkeypatch.setattr("gool_bot2.multi_menu.reconcile_pending", lambda: 0)
    monkeypatch.setattr(
        live_menu.FlashscoreProvider,
        "live_matches",
        lambda self: [_live("same001", minute=63, home_score=2, away_score=2)],
    )

    text = "\n".join(live_menu.strict_in_game_sections(path))

    assert "Открыто: <b>1</b>" in text
    assert "ТБ 0.5 @ 1.36" in text
    assert "1Т ТБ 0.5" not in text


def test_in_game_fails_closed_when_no_current_live_match_is_confirmed(tmp_path, monkeypatch):
    path = tmp_path / "journal.json"
    save_signal_journal(path, [_row("old001", "Finished Home", "Finished Away", minute=70)])
    monkeypatch.setattr("gool_bot2.multi_menu.reconcile_pending", lambda: 0)
    monkeypatch.setattr(live_menu.FlashscoreProvider, "live_matches", lambda self: [])

    text = "\n".join(live_menu.strict_in_game_sections(path))

    assert "Finished Home" not in text
    assert "подтверждённых Flashscore как LIVE" in text
