from pathlib import Path

from PIL import Image

from gool_bot2.v4_prematch_card import render_v4_parlay_card, render_v4_prematch_card, render_v4_prematch_result_card


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
        assert img.size == (1080, 1120)


def test_same_card_can_render_live_state(tmp_path, monkeypatch):
    row = _row()
    row.update({"lifecycle": "in_game", "current_minute": 17, "current_score": [1, 0]})
    monkeypatch.setattr("gool_bot2.signal_cards._save", lambda img: _save(img, tmp_path / "live.png"))
    path = Path(render_v4_prematch_card(row, in_game=True))
    with Image.open(path) as img:
        assert img.size == (1080, 1120)


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


def test_market_label_supports_dynamic_prematch_markets():
    from gool_bot2.v4_prematch_card import _market_label
    assert _market_label("over 3.5") == "ТБ 3.5"
    assert _market_label("under 1.5") == "ТМ 1.5"
    assert _market_label("BTTS_NO") == "Обе забьют — Нет"


def test_parlay_card_uses_four_team_badges(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr("gool_bot2.signal_cards._logo", lambda meta, side: calls.append((dict(meta), side)) or None)
    monkeypatch.setattr("gool_bot2.signal_cards._save", lambda img: _save(img, tmp_path / "parlay.png"))
    row = {
        "origin": "prematch_parlay", "kind": "DOUBLES", "odd": 2.48,
        "legs": [
            {"home":"A","away":"B","league":"League One","scheduled_start":"29.09 18:00 МСК","market":"FT_OVER_2.5","odd":1.55,"flashscore_meta":{"home_team_id":"a","away_team_id":"b"}},
            {"home":"C","away":"D","league":"League Two","scheduled_start":"29.09 21:45 МСК","market":"BTTS_YES","odd":1.60,"flashscore_meta":{"home_team_id":"c","away_team_id":"d"}},
        ],
    }
    path = Path(render_v4_parlay_card(row))
    with Image.open(path) as img:
        assert img.width == 1080
        assert img.height >= 780
    assert [side for _, side in calls] == ["home", "away", "home", "away"]


def test_parlay_result_card_renders_leg_statuses(tmp_path, monkeypatch):
    monkeypatch.setattr("gool_bot2.signal_cards._logo", lambda meta, side: None)
    monkeypatch.setattr("gool_bot2.signal_cards._save", lambda img: _save(img, tmp_path / "parlay_result.png"))
    row = {
        "origin":"prematch_parlay","kind":"DOUBLES","odd":2.48,"result":"lost",
        "legs":[
            {"home":"A","away":"B","league":"L1","scheduled_start":"29.09 18:00 МСК","market":"FT_OVER_2.5","odd":1.55,"result":"won","settled_score":[3,1]},
            {"home":"C","away":"D","league":"L2","scheduled_start":"29.09 21:45 МСК","market":"BTTS_YES","odd":1.60,"result":"lost","settled_score":[1,0]},
        ],
    }
    path = Path(render_v4_parlay_card(row, result=True))
    with Image.open(path) as img:
        assert img.width == 1080
