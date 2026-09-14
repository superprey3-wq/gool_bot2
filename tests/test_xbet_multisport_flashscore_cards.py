from __future__ import annotations

from io import BytesIO
from pathlib import Path

from PIL import Image

from gool_bot2.xbet_multisport_card import render_multisport_steam_card
from gool_bot2.xbet_multisport_steam import (
    MultiSportSteamWorker,
    SPORTS,
    map_xbet_to_flashscore,
    parse_flashscore_live,
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
