from pathlib import Path

from PIL import Image

from gool_bot2.v4_prematch_card import render_v4_prematch_card


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
