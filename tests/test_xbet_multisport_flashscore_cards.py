from __future__ import annotations

from io import BytesIO
from pathlib import Path

from PIL import Image

from gool_bot2.xbet_multisport_card import render_multisport_prematch_card, render_multisport_steam_card
from gool_bot2.hockey_signal_card import render_hockey_prematch_card
from gool_bot2.xbet_multisport_steam import (
    MultiSportSteamWorker,
    SPORTS,
    map_xbet_to_flashscore,
    parse_flashscore_live,
    parse_flashscore_events,
)


def test_flashscore_parser_keeps_only_live_rows():
    body = (
        "ZA÷NHL~"
        "AA÷Ab12Cd34¬AB÷2¬AC÷3¬AE÷Boston Bruins¬AF÷New York Rangers¬AG÷2¬AH÷1~"
        "AA÷Ef56Gh78¬AB÷3¬AE÷Finished Home¬AF÷Finished Away¬AG÷4¬AH÷2~"
    )
    rows = parse_flashscore_live(body)
    assert len(rows) == 1
    assert rows[0]["flashscore_event_id"] == "Ab12Cd34"
    assert rows[0]["score"] == [2, 1]
    assert rows[0]["league"] == "NHL"


def test_xbet_events_are_whitelisted_by_flashscore_pair():
    xbet = [
        {"I": "101", "O1": "Boston Bruins", "O2": "NY Rangers"},
        {"I": "102", "O1": "Fake Team", "O2": "Other Fake"},
    ]
    flashscore = [
        {
            "flashscore_event_id": "Ab12Cd34",
            "home": "Boston Bruins",
            "away": "New York Rangers",
            "score": [2, 1],
            "league": "NHL",
        }
    ]
    mapped = map_xbet_to_flashscore(xbet, flashscore, min_score=0.65, min_side=0.45)
    assert len(mapped) == 1
    assert mapped[0][0]["I"] == "101"
    assert mapped[0][1]["flashscore_event_id"] == "Ab12Cd34"


def test_reversed_provider_order_is_detected():
    xbet = [{"I": "101", "O1": "NY Rangers", "O2": "Boston Bruins"}]
    flashscore = [{
        "flashscore_event_id": "Ab12Cd34",
        "home": "Boston Bruins",
        "away": "New York Rangers",
        "score": [2, 1],
        "league": "NHL",
    }]
    mapped = map_xbet_to_flashscore(xbet, flashscore, min_score=0.65, min_side=0.45)
    assert len(mapped) == 1
    assert mapped[0][2] is True


def _hockey_game(score=(2, 1)):
    return {
        "I": "101",
        "O1": "Boston Bruins",
        "O2": "New York Rangers",
        "SC": {"FS": {"S1": score[0], "S2": score[1]}, "CPS": "3rd period", "TS": 420},
        "GE": [{"E": [[
            {"G": 4, "T": 9, "P": 6.5, "C": 1.80},
            {"G": 4, "T": 10, "P": 6.5, "C": 2.00},
        ]]}],
    }


def _fs(score=(2, 1)):
    return {
        "flashscore_event_id": "Ab12Cd34",
        "home": "Boston Bruins",
        "away": "New York Rangers",
        "score": [score[0], score[1]],
        "league": "NHL",
    }


def test_snapshot_requires_flashscore_score_sync(tmp_path: Path):
    worker = MultiSportSteamWorker(tmp_path)
    row, error = worker._snapshot(_hockey_game((2, 1)), _fs((3, 1)), False, 0.96, SPORTS["hockey"])
    assert row is None
    assert error == "score_mismatch"


def test_snapshot_uses_flashscore_identity_and_score(tmp_path: Path):
    worker = MultiSportSteamWorker(tmp_path)
    row, error = worker._snapshot(_hockey_game(), _fs(), False, 0.96, SPORTS["hockey"])
    assert error is None
    assert row is not None
    assert row["flashscore_event_id"] == "Ab12Cd34"
    assert row["flashscore_score_verified"] is True
    assert row["score"] == [2, 1]


def test_multisport_signal_card_is_png():
    cfg = SPORTS["hockey"]
    row = {
        "home": "Boston Bruins",
        "away": "New York Rangers",
        "league": "NHL",
        "score": [2, 1],
        "period": "3rd period",
        "clock_seconds": 420,
        "line": 6.5,
        "over": 1.80,
        "flashscore_event_id": "Ab12Cd34",
        "flashscore_match_score": 0.96,
    }
    signal = {
        "metric_delta": 0.62,
        "probability_delta_pp": 5.2,
        "line_delta": 0.5,
        "moves": 4,
        "age_seconds": 46,
        "extreme": False,
        "end": {"line": 6.5, "over": 1.80},
    }
    png = render_multisport_steam_card(row, signal, cfg)
    assert png.startswith(b"\x89PNG\r\n\x1a\n")
    image = Image.open(BytesIO(png))
    assert image.size == (1080, 920)


def test_multisport_prematch_card_is_distinct_png():
    cfg = SPORTS["basketball"]
    row = {
        "phase": "PREMATCH",
        "home": "Boston Celtics",
        "away": "New York Knicks",
        "league": "NBA",
        "start_ts": 1893456000,
        "line": 224.5,
        "over": 1.82,
        "under": 1.98,
        "flashscore_event_id": "Cd34Ef56",
        "flashscore_match_score": 0.94,
    }
    signal = {
        "phase": "PREMATCH",
        "direction": "under",
        "line": 224.5,
        "odd": 1.98,
        "metric_delta": 3.2,
        "probability_delta_pp": 3.8,
        "line_delta": 2.5,
        "moves": 3,
        "strength": 84.0,
        "extreme": False,
    }
    png = render_multisport_prematch_card(row, signal, cfg)
    assert png.startswith(b"\x89PNG\r\n\x1a\n")
    image = Image.open(BytesIO(png))
    assert image.size == (1080, 900)


def test_hockey_handicap_prematch_card_renders_png():
    cfg = SPORTS["hockey"]
    row = {
        "phase": "PREMATCH",
        "home": "Krylya Sovetov",
        "away": "Mikhaylov Academy U20",
        "league": "RUSSIA: MHL",
        "start_ts": 1893456000,
        "scope": "FULL_MATCH",
        "market_family": "handicap",
        "selection": "Ф1 +1.5",
        "opening_line": 1.0,
        "opening_odd": 1.66,
    }
    signal = {
        "phase": "PREMATCH",
        "direction": "home",
        "selection_side": "home",
        "selection": "Ф1 +1.5",
        "line": 1.5,
        "odd": 1.59,
        "metric_delta": 5.9,
        "probability_delta_pp": 5.9,
        "line_delta": 0.5,
        "moves": 3,
        "strength": 100.0,
        "extreme": True,
        "start": {"line": 1.0, "odd": 1.66},
    }
    png = render_hockey_prematch_card(row, signal, cfg)
    assert png.startswith(b"\x89PNG\r\n\x1a\n")
    image = Image.open(BytesIO(png))
    assert image.size == (1080, 900)


def test_multisport_delivery_uses_card_without_duplicate_caption(tmp_path, monkeypatch):
    worker = MultiSportSteamWorker(tmp_path)
    row = {
        "phase": "PREMATCH",
        "home": "Krylya Sovetov",
        "away": "Mikhaylov Academy U20",
        "league": "RUSSIA: MHL",
        "start_ts": 1893456000,
        "scope": "FULL_MATCH",
        "market_family": "handicap",
        "selection": "Ф1 +1.5",
    }
    signal = {
        "phase": "PREMATCH",
        "direction": "home",
        "selection_side": "home",
        "selection": "Ф1 +1.5",
        "line": 1.5,
        "odd": 1.59,
        "strength": 100.0,
        "metric_delta": 5.9,
        "probability_delta_pp": 5.9,
        "line_delta": 0.5,
        "moves": 3,
        "start": {"line": 1.0, "odd": 1.66},
    }
    seen = {}
    import gool_bot2.xbet_multisport_steam as steam
    monkeypatch.setattr(steam.telegram, "broadcast_photo", lambda png, caption="": seen.update({"caption": caption, "png": png}) or 1)
    sent = worker._deliver(row, signal, SPORTS["hockey"])
    assert sent == 1
    assert seen["caption"] == ""
    assert seen["png"].startswith(b"\x89PNG\r\n\x1a\n")


def test_multisport_flashscore_emblem_meta_reaches_hockey_card(monkeypatch):
    body = (
        "ZA÷MHL~"
        "AA÷Ab12Cd34¬AB÷1¬AE÷Krylya Sovetov¬AF÷Mikhaylov Academy U20"
        "¬JA÷home123¬JB÷away456¬WU÷krylya-sovetov¬WV÷mikhaylov-academy"
        "¬OA÷home-logo.png¬OB÷away-logo.png~"
    )
    row = parse_flashscore_events(body)[0]
    assert row["home_logo_file"] == "home-logo.png"
    assert row["away_logo_file"] == "away-logo.png"
    assert row["home_team_id"] == "home123"
    assert row["away_team_id"] == "away456"

    seen = []
    import gool_bot2.hockey_signal_card as card
    monkeypatch.setattr(card.sc, "_logo", lambda meta, side: seen.append((dict(meta), side)) or None)
    signal = {
        "phase":"PREMATCH","direction":"home","selection_side":"home",
        "selection":"Ф1 +1.5","line":1.5,"odd":1.59,
        "metric_delta":5.9,"probability_delta_pp":5.9,"line_delta":0.5,
        "moves":3,"strength":100.0,"start":{"line":1.0,"home":1.66},
    }
    png = render_hockey_prematch_card({
        **row,
        "phase":"PREMATCH","start_ts":1893456000,
        "scope":"FULL_MATCH","market_family":"handicap","selection":"Ф1 +1.5",
    }, signal, SPORTS["hockey"])
    assert png.startswith(b"\x89PNG\r\n\x1a\n")
    assert any(meta.get("home_logo_file") == "home-logo.png" and side == "home" for meta, side in seen)
    assert any(meta.get("away_logo_file") == "away-logo.png" and side == "away" for meta, side in seen)
