from __future__ import annotations

from gool_bot2 import multi_autonomous_steam
from gool_bot2.steam_red_card_guard import _red_context


def _record(red_home: int, red_away: int):
    return {
        "match": {
            "flashscore_event_id": "red-test",
            "minute": 61,
            "home_score": 1,
            "away_score": 1,
            "is_finished": False,
        },
        "providers": {
            "flashscore": {
                "stats": {
                    "red_cards": [red_home, red_away],
                    "shots": [8, 7],
                }
            }
        },
    }


def test_red_context_detects_current_dismissal():
    red = _red_context(_record(1, 0))
    assert red == {"has_red_card": True, "home_red": 1, "away_red": 0}


def test_autonomous_steam_is_blocked_when_red_card_present():
    record = _record(0, 1)
    rows = multi_autonomous_steam.build_autonomous_steam_candidates(
        record,
        {"dummy": True},
        data_quality=0.9,
    )
    assert rows == []
    guard = record["xbet_steam_red_card_guard"]
    assert guard["blocked"] is True
    assert guard["reason"] == "red_card_present"
    assert guard["away_red"] == 1
