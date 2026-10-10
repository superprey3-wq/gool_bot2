from __future__ import annotations

from pathlib import Path

from gool_bot2.football_v6_flashscore_quote import flashscore_fulltime_quote
from gool_bot2.football_v6_consensus_shadow import evaluate_football_v6_prematch
from gool_bot2.football_v5_shadow import score_grid


def _bookmaker(name, bid):
    return {"bookmaker": {"id": bid, "name": name}}


def _total(book, over=2.1, under=1.75, *, line=2.5):
    return {
        "bettingScope": "FULL_TIME", "bettingType": "OVER_UNDER", "bookmakerId": book,
        "odds": [
            {"selection": "OVER", "handicap": {"value": line}, "value": over, "active": True},
            {"selection": "UNDER", "handicap": {"value": line}, "value": under, "active": True},
        ],
    }


def _hda(book):
    return {
        "bettingScope": "FULL_TIME", "bettingType": "HOME_DRAW_AWAY", "bookmakerId": book,
        "odds": [
            {"eventParticipantId": "team-home", "value": 2.0},
            {"eventParticipantId": "team-away", "value": 4.2},
            {"value": 3.1},
        ],
    }


def _btts(book):
    return {
        "bettingScope": "FULL_TIME", "bettingType": "BOTH_TEAMS_TO_SCORE", "bookmakerId": book,
        "odds": [{"bothTeamsToScore": True, "value": 1.8},
                 {"bothTeamsToScore": False, "value": 2.0}],
    }


def test_real_flashscore_market_quote_same_bookmaker_and_full_time():
    payload = {
        "settings": {"bookmakers": [_bookmaker("Book A", "A"), _bookmaker("Book B", "B")]},
        "odds": [
            _total("A", 2.1, 1.75), _hda("A"), _btts("A"),
            _total("B", 3.8, 1.14),
            {**_total("A", 2.0, 2.0), "bettingScope": "FIRST_HALF"},
        ],
    }
    result = flashscore_fulltime_quote(payload)
    assert result is not None
    assert result["source"] == "flashscore_actual_fulltime_odds"
    assert result["bookmaker"] == "Book A"
    assert result["match_totals"] == [{"line": 2.5, "over": 2.1, "under": 1.75}]
    assert result["match_1x2"] == {"home": 2.0, "draw": 3.1, "away": 4.2}
    assert result["btts"] == {"yes": 1.8, "no": 2.0}


def test_never_pair_two_different_bookmakers():
    over_only = _total("A")
    over_only["odds"] = over_only["odds"][:1]
    under_only = _total("B")
    under_only["odds"] = under_only["odds"][1:]
    payload = {
        "settings": {"bookmakers": [_bookmaker("One", "A"), _bookmaker("Two", "B")]},
        "odds": [over_only, under_only],
    }
    assert flashscore_fulltime_quote(payload) is None


def test_no_half_or_asian_lines_and_no_fabricated_odds():
    bad = _total("A", line=2.25)
    assert flashscore_fulltime_quote({"odds": [bad]}) is None
    missing = _total("A")
    missing["odds"][1]["active"] = False
    assert flashscore_fulltime_quote({"odds": [missing]}) is None
    assert flashscore_fulltime_quote({}) is None


def test_v6_shadow_price_uses_only_real_verified_source():
    data = {"settings":{"bookmakers":[_bookmaker("Real book", "A")]},
            "odds":[_total("A", over=2.5, under=1.55), _hda("A")]}
    quote = flashscore_fulltime_quote(data)
    forecast = {
        "status":"READY", "home_lambda":2.2, "away_lambda":0.9,
        "quality":0.9, "probabilities":score_grid(2.2,0.9),
    }
    profile = {"full_match":{"home_expected_goals":2.1,"away_expected_goals":1.0}}
    result = evaluate_football_v6_prematch(forecast,profile,quote,event_id="real-123")
    assert result["quoted_markets_checked"] >= 4
    assert not result["published"]


def test_production_and_weekend_pipeline_use_flashscore_fallback():
    root = Path(__file__).resolve().parents[1]
    production = (root/"scripts/gool_flashscore_today.py").read_text("utf-8")
    weekend = (root/"scripts/gool_football_v5_weekend.py").read_text("utf-8")
    assert "market or flashscore_fulltime_quote(odds_payload)" in production
    assert "fs_quote_available" in weekend
    assert "get_fs_quote" in weekend
