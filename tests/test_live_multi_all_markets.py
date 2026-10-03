from datetime import datetime, timezone

from gool_bot2.live_multi_all_markets import analyze_live_all_markets


def _fresh():
    return datetime.now(timezone.utc).isoformat()


def _record(minute, score, stats, momentum=None):
    return {
        "match": {
            "flashscore_event_id": "m1",
            "home": "Home",
            "away": "Away",
            "league": "Test",
            "minute": minute,
            "home_score": score[0],
            "away_score": score[1],
            "is_halftime": False,
            "is_finished": False,
        },
        "providers": {
            "flashscore": {"stats": stats},
            "fotmob": {"stats": stats},
        },
        "live_momentum": momentum or {},
    }


def test_35min_wild_game_can_choose_full_match_over_instead_of_forcing_first_half_goal():
    record = _record(
        35,
        (0, 0),
        {
            "xg": [0.8, 0.7],
            "shots": [8, 7],
            "shots_on_target": [3, 3],
            "big_chances": [1, 1],
            "shots_inside_box": [5, 4],
            "touches_box": [13, 11],
            "corners": [4, 3],
        },
        {
            "minutes_in_epoch": 6,
            "xg_total_last_5m": 0.35,
            "shots_total_last_5m": 5,
            "sot_total_last_5m": 2,
            "big_total_last_5m": 1,
        },
    )
    market = {
        "score_home": 0,
        "score_away": 0,
        "captured_at": _fresh(),
        "markets": {
            "match_total": [
                {"line": 1.5, "over": 1.75, "under": 2.05},
                {"line": 2.5, "over": 2.35, "under": 1.55},
            ],
            "first_half_total": [{"line": 0.5, "over": 2.00, "under": 1.78}],
            "home_total": [],
            "away_total": [],
            "btts": {"yes": 2.15, "no": 1.62},
            "match_1x2": {},
        },
        "pressure": {},
    }

    decision = analyze_live_all_markets(record, market)

    assert decision.status == "BET"
    assert decision.winner is not None
    assert decision.winner.family == "match_total"
    assert decision.winner.selection == "over"
    assert decision.winner.label in {"ТБ 1.5", "ТБ 2.5"}
    # The all-market arbiter exposes one final winner, not a public opposite alternative.
    assert not hasattr(decision, "alternatives")


def test_70min_one_one_dead_epoch_can_choose_under():
    record = _record(
        70,
        (1, 1),
        {
            "xg": [0.45, 0.35],
            "shots": [4, 3],
            "shots_on_target": [1, 1],
            "big_chances": [0, 0],
            "shots_inside_box": [2, 2],
            "touches_box": [8, 7],
            "corners": [2, 2],
        },
        {
            "minutes_in_epoch": 8,
            "xg_total_last_5m": 0.02,
            "shots_total_last_5m": 1,
            "sot_total_last_5m": 0,
            "big_total_last_5m": 0,
        },
    )
    market = {
        "score_home": 1,
        "score_away": 1,
        "captured_at": _fresh(),
        "markets": {
            "match_total": [
                {"line": 2.5, "over": 1.82, "under": 2.02},
                {"line": 3.5, "over": 3.00, "under": 1.46},
            ],
            "first_half_total": [],
            "home_total": [{"line": 1.5, "over": 2.55, "under": 1.48}],
            "away_total": [{"line": 1.5, "over": 2.65, "under": 1.45}],
            "btts": {"yes": 1.01, "no": 20.0},
            "match_1x2": {
                "home": 3.00,
                "draw": 1.90,
                "away": 3.20,
                "fair": {"home": 0.276, "draw": 0.493, "away": 0.231},
            },
        },
        "pressure": {},
    }

    decision = analyze_live_all_markets(record, market)

    assert decision.status == "BET"
    assert decision.winner is not None
    assert decision.winner.thesis == "goals_down"
    assert decision.winner.selection == "under"
    assert decision.winner.family in {"match_total", "home_total", "away_total"}


def test_score_desync_is_hard_wait():
    record = _record(
        52,
        (2, 1),
        {"xg": [1.0, 0.7], "shots": [9, 7], "shots_on_target": [4, 2]},
        {"minutes_in_epoch": 6, "shots_total_last_5m": 4, "sot_total_last_5m": 2},
    )
    market = {
        "score_home": 1,
        "score_away": 1,
        "captured_at": _fresh(),
        "markets": {},
        "pressure": {},
    }

    decision = analyze_live_all_markets(record, market)

    assert decision.status == "WAIT"
    assert decision.winner is None
    assert decision.reason == "score_desync"
