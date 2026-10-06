from gool_bot2.basketball_brain_v2 import live_signal as basketball_live_signal
from gool_bot2.hockey_brain_v2 import live_signal as hockey_live_signal
from gool_bot2.providers.flashscore import FlashscoreProvider
from gool_bot2.segment_memory import build_segment_memory, remaining_match_projection


def _basket_segments(a1, a2, a3, a4, b1, b2, b3, b4):
    return {
        "QUARTER_1": [a1, b1],
        "QUARTER_2": [a2, b2],
        "QUARTER_3": [a3, b3],
        "QUARTER_4": [a4, b4],
    }


def _hockey_segments(h1, h2, h3, a1, a2, a3):
    return {
        "PERIOD_1": [h1, a1],
        "PERIOD_2": [h2, a2],
        "PERIOD_3": [h3, a3],
    }


def test_flashscore_segment_parser_reads_real_quarter_and_period_line_scores():
    basketball = (
        "AC÷1st Quarter¬IG÷24¬IH÷20¬~"
        "AC÷2nd Quarter¬IG÷28¬IH÷25¬~"
        "AC÷3rd Quarter¬IG÷19¬IH÷21¬~"
        "AC÷4th Quarter¬IG÷18¬IH÷17¬~"
        "AC÷Overtime¬IG÷8¬IH÷8¬~"
    )
    assert FlashscoreProvider.parse_segment_scores(basketball, "basketball") == {
        "QUARTER_1": [24, 20],
        "QUARTER_2": [28, 25],
        "QUARTER_3": [19, 21],
        "QUARTER_4": [18, 17],
    }

    hockey = (
        "AC÷1st Period¬IG÷1¬IH÷0¬~"
        "AC÷2nd Period¬IG÷0¬IH÷2¬~"
        "AC÷3rd Period¬IG÷1¬IH÷1¬~"
    )
    assert FlashscoreProvider.parse_segment_scores(hockey, "hockey") == {
        "PERIOD_1": [1, 0],
        "PERIOD_2": [0, 2],
        "PERIOD_3": [1, 1],
    }


def test_segment_memory_keeps_recent_form_and_direct_h2h_as_separate_rows():
    home_recent = [
        {
            "event_id": f"ha{i}",
            "home": "Team A",
            "away": f"Other {i}",
            "segments": _basket_segments(24, 25, 18, 17, 20, 21, 20, 19),
        }
        for i in range(5)
    ]
    away_recent = [
        {
            "event_id": f"bb{i}",
            "home": f"Other B {i}",
            "away": "Team B",
            "segments": _basket_segments(22, 21, 20, 19, 27, 28, 22, 21),
        }
        for i in range(5)
    ]
    h2h = [
        {
            "event_id": f"h2h{i}",
            "home": "Team A",
            "away": "Team B",
            "segments": _basket_segments(23, 24, 17, 16, 28, 29, 20, 19),
        }
        for i in range(3)
    ]
    memory = build_segment_memory(
        {"home_recent": home_recent, "away_recent": away_recent, "h2h": h2h},
        "Team A",
        "Team B",
        "basketball",
    )

    q2 = memory["segments"]["QUARTER_2"]
    q4 = memory["segments"]["QUARTER_4"]
    assert q2["home_recent_for"] == 25.0
    assert q2["away_recent_for"] == 28.0
    assert q2["h2h_home_for"] == 24.0
    assert q2["h2h_away_for"] == 29.0
    assert q2["h2h_n"] == 3
    assert q2["h2h_weight"] == 0.30
    assert q2["expected_total"] > q4["expected_total"]
    assert memory["quality"] > 0.5


def test_remaining_projection_can_detect_hot_first_half_but_weak_second_half():
    memory = {
        "sport": "basketball",
        "quality": 0.9,
        "expected_match_total": 186.0,
        "segments": {
            "QUARTER_1": {"expected_home": 25.0, "expected_away": 25.0, "expected_total": 50.0},
            "QUARTER_2": {"expected_home": 30.0, "expected_away": 28.0, "expected_total": 58.0},
            "QUARTER_3": {"expected_home": 20.0, "expected_away": 20.0, "expected_total": 40.0},
            "QUARTER_4": {"expected_home": 18.0, "expected_away": 20.0, "expected_total": 38.0},
        },
    }
    result = remaining_match_projection(
        memory,
        scope="QUARTER_3",
        match_score=[60, 55],
        current_segment_score=[0, 0],
        current_segment_projection=40.0,
    )
    assert result is not None
    # Team A already has 60, but its Q3+Q4 profile adds only about 38.
    assert result["home"] == 98.0
    assert result["total"] == 193.0
    assert result["remaining_home"] == 38.0


def _basketball_memory():
    return {
        "sport": "basketball",
        "quality": 0.85,
        "expected_match_total": 178.0,
        "segments": {
            "QUARTER_1": {
                "expected_home": 24.0, "expected_away": 23.0, "expected_total": 47.0,
                "h2h_home_for": 25.0, "h2h_away_for": 22.0, "h2h_n": 4,
            },
            "QUARTER_2": {
                "expected_home": 25.0, "expected_away": 24.0, "expected_total": 49.0,
                "h2h_home_for": 26.0, "h2h_away_for": 23.0, "h2h_n": 4,
            },
            "QUARTER_3": {
                "expected_home": 20.0, "expected_away": 20.0, "expected_total": 40.0,
                "h2h_home_for": 19.0, "h2h_away_for": 20.0, "h2h_n": 4,
            },
            "QUARTER_4": {
                "expected_home": 18.0, "expected_away": 19.0, "expected_total": 37.0,
                "h2h_home_for": 17.0, "h2h_away_for": 19.0, "h2h_n": 4,
            },
        },
    }


def test_basketball_live_can_take_full_match_team_under_from_remaining_schedule():
    brain = {
        "brain_state": "PASS",
        "brain_score": 78.0,
        "scope": "QUARTER_3",
        "history_points": 3,
        "recent_window_seconds": 60.0,
        "recent_score_rate": 4.0,
        "recent_possessions_per_min": 2.2,
        "break_transition": False,
        "league": "TURKEY: Super Lig",
        "score": [70, 65],
        "current_segment_score": [10, 10],
        "segment_memory": _basketball_memory(),
        "live_game_stats": {
            "current_segment_available": True,
            "stats_mode": "direct_segment",
            "segment_stats": {
                "field_goals": [5.0, 5.0],
                "three_point_field_goals": [2.0, 2.0],
                "free_throws": [1.0, 1.0],
                "offensive_rebounds": [2.0, 2.0],
                "defensive_rebounds": [7.0, 7.0],
                "turnovers": [2.0, 2.0],
                "fouls": [2.0, 2.0],
            },
            "segment_attempts": {
                "field_goals": [10.0, 10.0],
                "three_point_field_goals": [4.0, 4.0],
                "free_throws": [2.0, 2.0],
            },
        },
    }
    lane = {
        "scope": "FULL_MATCH",
        "market_family": "home_total",
        "clock_seconds": 300.0,
        "line": 105.5,
        "score": [70, 65],
        "match_score": [70, 65],
        "league": "TURKEY: Super Lig",
        "probability": 0.50,
        "over": 1.90,
        "under": 1.90,
    }
    signal = basketball_live_signal(brain, lane)
    assert signal is not None
    assert signal["direction"] == "under"
    assert signal["projected_total"] < 105.5
    assert signal["remaining_match_projection"]["home"] < 105.5
    assert signal["segment_h2h_n"] == 4
    assert signal["historical_confirmation"] is True


def test_hockey_live_can_price_full_match_total_from_period_memory():
    memory = {
        "sport": "hockey",
        "quality": 0.85,
        "expected_match_total": 5.0,
        "segments": {
            "PERIOD_1": {
                "expected_home": 1.0, "expected_away": 0.8, "expected_total": 1.8,
                "h2h_home_for": 1.0, "h2h_away_for": 1.0, "h2h_n": 4,
            },
            "PERIOD_2": {
                "expected_home": 0.8, "expected_away": 0.7, "expected_total": 1.5,
                "h2h_home_for": 0.8, "h2h_away_for": 0.6, "h2h_n": 4,
            },
            "PERIOD_3": {
                "expected_home": 0.8, "expected_away": 0.9, "expected_total": 1.7,
                "h2h_home_for": 0.7, "h2h_away_for": 0.8, "h2h_n": 4,
            },
        },
    }
    brain = {
        "brain_state": "PASS",
        "brain_score": 76.0,
        "scope": "PERIOD_2",
        "history_points": 3,
        "recent_window_seconds": 60.0,
        "recent_shot_rate": 0.5,
        "recent_blocked_rate": 0.0,
        "recent_penalty_delta": 0.0,
        "recent_pp_goal_delta": 0.0,
        "league": "KHL",
        "score": [3, 2],
        "current_segment_score": [1, 0],
        "segment_score_verified": True,
        "elapsed_seconds": 600.0,
        "prematch_match_lambda": 5.05,
        "segment_memory": memory,
        "live_game_stats": {
            "current_segment_available": True,
            "stats_mode": "cumulative_through_current_segment",
            "segment_stats": {"shots_on_goal": [15, 13]},
        },
    }
    lane = {
        "scope": "FULL_MATCH",
        "market_family": "match_total",
        "clock_seconds": 600.0,
        "line": 8.5,
        "score": [3, 2],
        "match_score": [3, 2],
        "league": "KHL",
        "probability": 0.50,
        "over": 1.90,
        "under": 1.90,
    }
    signal = hockey_live_signal(brain, lane)
    assert signal is not None
    assert signal["direction"] == "under"
    assert signal["projected_total"] < 8.5
    assert signal["segment_h2h_n"] == 4
