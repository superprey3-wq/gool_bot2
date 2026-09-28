from pathlib import Path

from PIL import Image

from gool_bot2.v4_prematch_card import render_v4_prematch_card, render_v4_prematch_result_card


def _row():
    return {
        "origin": "prematch",
        "home": "Arsenal",
        "away": "Chelsea",
        "league": "Premier League",
        "market": "ТБ 2.5",
        "odd": 1.85,
        "probability": 0.68,
        "edge": 0.055,
        "tier": "STRONG",
        "scheduled_start": "19:30",
    }


def test_prematch_card_renders(tmp_path, monkeypatch):
    monkeypatch.setattr("gool_bot2.signal_cards._save", lambda img: _save(img, tmp_path / "pre.png"))
    path = Path(render_v4_prematch_card(_row()))
    with Image.open(path) as img:
        assert img.size == (1080, 980)


def test_same_card_can_render_live_state(tmp_path, monkeypatch):
    row = _row()
    row.update({"lifecycle": "in_game", "current_minute": 17, "current_score": [1, 0]})
    monkeypatch.setattr("gool_bot2.signal_cards._save", lambda img: _save(img, tmp_path / "live.png"))
    path = Path(render_v4_prematch_card(row, in_game=True))
    with Image.open(path) as img:
        assert img.size == (1080, 980)


def _save(img, path):
    img.save(path)
    return str(path)


def test_prematch_card_uses_flashscore_team_badges(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr("gool_bot2.signal_cards._logo", lambda meta, side: calls.append((dict(meta), side)) or None)
    monkeypatch.setattr("gool_bot2.signal_cards._save", lambda img: _save(img, tmp_path / "badges.png"))
    row = _row()
    row["flashscore_meta"] = {
        "home_team_id": "arsenal-id",
        "away_team_id": "chelsea-id",
        "home_team_slug": "arsenal",
        "away_team_slug": "chelsea",
    }

    render_v4_prematch_card(row)

    assert [side for _, side in calls] == ["home", "away"]
    assert calls[0][0]["home_team_id"] == "arsenal-id"
    assert calls[1][0]["away_team_id"] == "chelsea-id"


def test_result_cards_render_win_and_loss(tmp_path, monkeypatch):
    monkeypatch.setattr("gool_bot2.signal_cards._logo", lambda meta, side: None)
    paths = iter([tmp_path / "won.png", tmp_path / "lost.png"])
    monkeypatch.setattr("gool_bot2.signal_cards._save", lambda img: _save(img, next(paths)))
    base = _row()
    base["settled_score"] = [2, 1]

    won = Path(render_v4_prematch_result_card({**base, "result": "won"}))
    lost = Path(render_v4_prematch_result_card({**base, "result": "lost"}))

    with Image.open(won) as img:
        assert img.size == (1080, 900)
    with Image.open(lost) as img:
        assert img.size == (1080, 900)
