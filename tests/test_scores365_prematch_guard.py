from __future__ import annotations

from gool_bot2.providers.scores365_prematch_guard import _halftime_score, _history_row


def _finished_game():
    return {
        "id": 12345,
        "statusGroup": 4,
        "startTime": "2026-08-20T16:00:00+00:00",
        "homeCompetitor": {"id": 1, "name": "Team A", "score": 3.0},
        "awayCompetitor": {"id": 2, "name": "Team B", "score": 1.0},
        "stages": [
            {
                "id": 7,
                "name": "Halftime",
                "shortName": "HT",
                "homeCompetitorScore": 1.0,
                "awayCompetitorScore": 0.0,
                "isEnded": True,
            },
            {
                "id": 9,
                "name": "End of 90 Minutes",
                "shortName": "90 Min.",
                "homeCompetitorScore": 3.0,
                "awayCompetitorScore": 1.0,
                "isEnded": True,
            },
        ],
    }


def test_extracts_365_halftime_stage():
    assert _halftime_score(_finished_game()) == (1, 0)


def test_normalizes_finished_365_game_into_two_halves():
    row = _history_row(_finished_game())

    assert row is not None
    assert row["home_score"] == 3
    assert row["away_score"] == 1
    assert row["halftime_home_score"] == 1
    assert row["halftime_away_score"] == 0
    assert row["second_half_home_score"] == 2
    assert row["second_half_away_score"] == 1
    assert row["source"] == "365scores_recent_halves"


def test_rejects_live_or_invalid_half_score_history():
    live = _finished_game()
    live["statusGroup"] = 3
    live["stages"][1]["isEnded"] = False
    assert _history_row(live) is None

    invalid = _finished_game()
    invalid["stages"][0]["homeCompetitorScore"] = 4.0
    assert _history_row(invalid) is None
