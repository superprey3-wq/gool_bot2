from __future__ import annotations

from io import BytesIO
from pathlib import Path

from PIL import Image

from gool_bot2.xbet_multisport_card import render_multisport_prematch_card, render_multisport_steam_card
from gool_bot2.hockey_signal_card import render_hockey_prematch_card
from gool_bot2.basketball_signal_card import render_basketball_prematch_card
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


def test_flashscore_parser_keeps_multisport_team_logo_metadata():
    body = (
        "ZA÷NBA~"
        "AA÷Ab12Cd34¬AB÷1¬AE÷Denver Nuggets¬AF÷Utah Jazz"
        "¬JA÷HOMEID¬JB÷AWAYID¬WU÷denver-nuggets¬WV÷utah-jazz"
        "¬OA÷home-logo.png¬OB÷away-logo.png~"
    )
    rows = __import__("gool_bot2.xbet_multisport_steam", fromlist=["parse_flashscore_events"]).parse_flashscore_events(body)
    assert rows[0]["home_team_id"] == "HOMEID"
    assert rows[0]["away_team_id"] == "AWAYID"
    assert rows[0]["home_logo_file"] == "home-logo.png"
    assert rows[0]["away_logo_file"] == "away-logo.png"


def test_hockey_and_basketball_cards_request_team_emblems(monkeypatch):
    import gool_bot2.signal_cards as sc
    calls = []
    def fake_logo(meta, side):
        calls.append((side, meta.get(f"{side}_logo_file")))
        return Image.new("RGBA", (32, 32), (255,255,255,255))
    monkeypatch.setattr(sc, "_logo", fake_logo)

    base = {
        "phase":"PREMATCH","home":"Home Club","away":"Away Club","league":"League",
        "start_ts":1893456000,"scope":"FULL_MATCH","market_family":"match_total",
        "home_logo_file":"home.png","away_logo_file":"away.png",
    }
    signal = {
        "phase":"PREMATCH","direction":"over","selection":"ТБ 5.5","line":5.5,"odd":1.80,
        "metric_delta":1.0,"probability_delta_pp":3.0,"line_delta":0.5,"moves":3,"strength":85,
        "start":{"line":5.0,"over":1.90},
    }
    assert render_hockey_prematch_card(base, signal, SPORTS["hockey"]).startswith(b"\x89PNG")
    basket_signal = {**signal, "selection":"ТБ 160.5", "line":160.5}
    assert render_basketball_prematch_card(base, basket_signal, SPORTS["basketball"]).startswith(b"\x89PNG")
    assert ("home","home.png") in calls
    assert ("away","away.png") in calls

def test_multisport_signal_journal_persists_emblem_metadata(tmp_path: Path, monkeypatch):
    from gool_bot2.multisport_journal import load_journal

    monkeypatch.setenv("GOOL_MULTISPORT_MODE", "shadow")
    worker = MultiSportSteamWorker(tmp_path)
    row = {
        "phase": "PREMATCH",
        "origin": "multisport_prematch",
        "sport": "hockey",
        "event_id": "XB101",
        "flashscore_event_id": "FS101",
        "home": "Home Club",
        "away": "Away Club",
        "league": "League",
        "start_ts": 1893456000,
        "scope": "FULL_MATCH",
        "market_family": "match_total",
        "home_team_id": "HOMEID",
        "away_team_id": "AWAYID",
        "home_team_slug": "home-club",
        "away_team_slug": "away-club",
        "home_logo_file": "home.png",
        "away_logo_file": "away.png",
    }
    signal = {
        "direction": "over",
        "selection": "ТБ 5.5",
        "line": 5.5,
        "odd": 1.80,
        "strength": 85.0,
        "metric_delta": 1.0,
        "probability_delta_pp": 3.5,
        "line_delta": 0.5,
        "moves": 3,
        "start": {"line": 5.0, "over": 1.90},
    }

    recorded, sent = worker._record_signal(row, signal, SPORTS["hockey"])

    assert recorded is True
    assert sent == 0
    saved = load_journal(worker.journal_path)
    assert len(saved) == 1
    assert saved[0]["home_team_id"] == "HOMEID"
    assert saved[0]["away_team_id"] == "AWAYID"
    assert saved[0]["home_logo_file"] == "home.png"
    assert saved[0]["away_logo_file"] == "away.png"

