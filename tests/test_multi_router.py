from __future__ import annotations

from datetime import datetime, timezone

from gool_bot2.multi_router import analyze_multi_match, build_goal_market_candidates, route_market


def _fresh() -> str:
    return datetime.now(timezone.utc).isoformat()


def _market_row() -> dict:
    return {
        "score_home": 1,
        "score_away": 2,
        "captured_at": _fresh(),
        "markets": {
            "match_total": [
                {"line": 3.5, "over": 1.30, "under": 3.35},
                # This integer line may exist in synthetic/legacy data but the
                # router must never build an Asian candidate from it.
                {"line": 4.0, "over": 1.60, "under": 2.20},
                {"line": 4.5, "over": 1.90, "under": 1.88},
            ],
            "first_half_total": [],
            "home_total": [
                {"line": 1.5, "over": 1.72, "under": 2.05},
            ],
            "away_total": [
                {"line": 2.5, "over": 2.05, "under": 1.72},
            ],
            "btts": {"yes": 1.50, "no": 2.45},
        },
        "pressure": {
            "match_total:3.5": {"prob_delta_pp": 1.0},
            "match_total:4.0": {"prob_delta_pp": 6.5},
            "match_total:4.5": {"prob_delta_pp": 2.0},
            "home_total:1.5": {"prob_delta_pp": 4.0},
            "away_total:2.5": {"prob_delta_pp": 1.0},
        },
    }


def _experts() -> dict:
    return {
        "another_goal": {"probability": 0.82, "source": "another_goal_model"},
        "two_more_goals": {"probability": 0.55, "source": "two_more_model"},
        "home_goal": {"probability": 0.66, "source": "home_goal_model"},
        "away_goal": {"probability": 0.42, "source": "away_goal_model"},
    }


def test_router_builds_only_classic_half_goal_totals_not_asian_middle():
    match = {"minute": 54, "home_score": 1, "away_score": 2}

    candidates = build_goal_market_candidates(match, _market_row(), _experts(), data_quality=0.92)

    assert candidates
    assert all(row.family != "asian_match_total" for row in candidates)
    assert all(row.strategy != "combined_total" for row in candidates)
    match_totals = [row for row in candidates if row.family == "match_total"]
    assert {row.label for row in match_totals} == {"ТБ 3.5", "ТБ 4.5"}
    assert all(abs((float(row.key.split(":")[-1]) % 1.0) - 0.5) < 1e-9 for row in match_totals)


def test_first_half_goal_model_uses_real_first_half_total():
    match = {"minute": 20, "home_score": 1, "away_score": 0}
    market = {
        "score_home": 1,
        "score_away": 0,
        "captured_at": _fresh(),
        "markets": {
            "match_total": [{"line": 1.5, "over": 1.38, "under": 2.90}],
            "first_half_total": [{"line": 1.5, "over": 1.72, "under": 2.02}],
            "home_total": [],
            "away_total": [],
            "btts": {},
        },
        "pressure": {
            "first_half_total:1.5": {"prob_delta_pp": 4.2, "one_way_moves": 2, "old_odd": 1.88},
        },
    }
    experts = {
        "another_goal": {"probability": 0.77, "source": "another_goal"},
        "goal_before_ht": {"probability": 0.71, "source": "goal_before_ht"},
    }

    candidates = build_goal_market_candidates(match, market, experts, data_quality=0.9)

    ht = [row for row in candidates if row.family == "first_half_total"]
    assert len(ht) == 1
    assert ht[0].strategy == "goal_before_ht"
    assert ht[0].label == "1Т ТБ 1.5"
    assert ht[0].odd == 1.72
    assert ht[0].correlation_key == "any_next_goal"


def test_late_router_chooses_best_one_goal_market_and_blocks_two_goal_paths():
    match = {"minute": 74, "home_score": 1, "away_score": 2}

    decision = analyze_multi_match(match, _market_row(), _experts(), data_quality=0.92)

    assert decision.status == "BET"
    assert decision.winner is not None
    assert decision.winner.goals_to_win == 1
    assert decision.winner.label == "ИТБ1 1.5"
    assert decision.winner.family == "team_total"
    late = [row for row in decision.rejected if row.goals_to_win >= 2]
    assert late
    assert all(any(block.startswith("two_goal_window_closed:74>65") for block in row.blocks) for row in late)


def test_another_goal_remains_open_through_85_and_closes_after():
    market = {
        "score_home": 1,
        "score_away": 1,
        "captured_at": _fresh(),
        "markets": {
            "match_total": [{"line": 2.5, "over": 2.05, "under": 1.70}],
            "first_half_total": [],
            "home_total": [],
            "away_total": [],
            "btts": {},
        },
        "pressure": {"match_total:2.5": {"prob_delta_pp": 2.0, "one_way_moves": 1}},
    }
    experts = {"another_goal": {"probability": 0.68, "source": "another_goal", "passed": True}}

    at_85 = analyze_multi_match(
        {"minute": 85, "home_score": 1, "away_score": 1}, market, experts, data_quality=0.95
    )
    after_85 = analyze_multi_match(
        {"minute": 86, "home_score": 1, "away_score": 1}, market, experts, data_quality=0.95
    )

    assert at_85.status == "BET"
    assert at_85.winner is not None
    assert at_85.winner.strategy == "another_goal"
    assert after_85.status == "WAIT"
    assert any(
        any(block.startswith("another_goal_window_closed:86>85") for block in row.blocks)
        for row in after_85.rejected
    )


def test_btts_and_scoreless_team_total_share_one_correlation_slot():
    match = {"minute": 57, "home_score": 1, "away_score": 0}
    market = {
        "score_home": 1,
        "score_away": 0,
        "captured_at": _fresh(),
        "markets": {
            "match_total": [{"line": 1.5, "over": 1.42, "under": 2.70}],
            "first_half_total": [],
            "home_total": [{"line": 1.5, "over": 1.85, "under": 1.90}],
            "away_total": [{"line": 0.5, "over": 1.82, "under": 1.95}],
            "btts": {"yes": 1.96, "no": 1.78},
        },
        "pressure": {},
    }
    experts = {
        "another_goal": 0.76,
        "two_more_goals": 0.43,
        "home_goal": 0.55,
        "away_goal": 0.64,
    }

    candidates = build_goal_market_candidates(match, market, experts, data_quality=0.9)
    decision = route_market(candidates, minute=57, score=(1, 0))

    visible = [decision.winner, *decision.alternatives]
    visible = [row for row in visible if row is not None]
    scoreless_goal_markets = [row for row in visible if row.correlation_key == "away_next_goal"]
    assert len(scoreless_goal_markets) == 1
    assert scoreless_goal_markets[0].label == "ОЗ — Да"


def test_score_desync_produces_wait_instead_of_guessing_market():
    match = {"minute": 52, "home_score": 2, "away_score": 2}

    decision = analyze_multi_match(match, _market_row(), _experts(), data_quality=1.0)

    assert decision.status == "WAIT"
    assert decision.winner is None
    assert decision.alternatives == []
