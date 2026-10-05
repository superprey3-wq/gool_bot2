from gool_bot2.basketball_brain_v2 import (
    live_candidate_gate,
    live_signal,
    prematch_candidate,
    prematch_signal,
    recent_possession_metrics,
)


def _neutral_features():
    return {
        "home_recent_n": 10,
        "away_recent_n": 10,
        "home_gf_avg": 83.0,
        "home_ga_avg": 83.0,
        "away_gf_avg": 83.0,
        "away_ga_avg": 83.0,
        "home_venue_gf_avg": 83.0,
        "home_venue_ga_avg": 83.0,
        "away_venue_gf_avg": 83.0,
        "away_venue_ga_avg": 83.0,
        "recent_total_avg": 166.0,
        "venue_total_avg": 166.0,
        "h2h_total_avg": 166.0,
        "home_rest_days": 2.0,
        "away_rest_days": 2.0,
        "rest_advantage_days": 0.0,
    }


def _strong_home_features():
    return {
        "home_recent_n": 10,
        "away_recent_n": 10,
        "home_gf_avg": 101.0,
        "home_ga_avg": 72.0,
        "away_gf_avg": 72.0,
        "away_ga_avg": 101.0,
        "home_venue_gf_avg": 104.0,
        "home_venue_ga_avg": 70.0,
        "away_venue_gf_avg": 70.0,
        "away_venue_ga_avg": 103.0,
        "recent_total_avg": 174.0,
        "venue_total_avg": 174.0,
        "h2h_total_avg": 168.0,
        "home_rest_days": 2.0,
        "away_rest_days": 1.0,
        "rest_advantage_days": 1.0,
    }


def _high_total_features():
    return {
        "home_recent_n": 10,
        "away_recent_n": 10,
        "home_gf_avg": 103.0,
        "home_ga_avg": 98.0,
        "away_gf_avg": 101.0,
        "away_ga_avg": 99.0,
        "home_venue_gf_avg": 105.0,
        "home_venue_ga_avg": 97.0,
        "away_venue_gf_avg": 100.0,
        "away_venue_ga_avg": 100.0,
        "recent_total_avg": 202.0,
        "venue_total_avg": 203.0,
        "h2h_total_avg": 198.0,
        "home_rest_days": 2.0,
        "away_rest_days": 2.0,
        "rest_advantage_days": 0.0,
    }


def _direct_stats(*, low_efficiency=False):
    if low_efficiency:
        fg_made = [3.0, 3.0]
        three_made = [0.0, 1.0]
    else:
        fg_made = [5.0, 5.0]
        three_made = [2.0, 2.0]
    return {
        "current_segment_available": True,
        "stats_mode": "direct_segment",
        "segment_stats": {
            "field_goals": fg_made,
            "three_point_field_goals": three_made,
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
    }


def _brain(*, scope="QUARTER_3", current=(10, 10), recent_poss=2.2, recent_score=4.0, payload=None):
    return {
        "brain_state": "PASS",
        "scope": scope,
        "history_points": 3,
        "recent_window_seconds": 60.0,
        "recent_score_rate": recent_score,
        "recent_possessions_per_min": recent_poss,
        "break_transition": False,
        "league": "TURKEY: Super Lig",
        "score": [50, 50],
        "current_segment_score": list(current),
        "live_game_stats": payload or _direct_stats(),
    }


def _lane(*, scope="QUARTER_3", elapsed=300, score=(10, 10), line=37.5, market_over=0.50, match_score=(50, 50)):
    return {
        "scope": scope,
        "market_family": "match_total",
        "clock_seconds": elapsed,
        "line": line,
        "score": list(score),
        "match_score": list(match_score),
        "league": "TURKEY: Super Lig",
        "probability": market_over,
        "over": 1.90,
        "under": 1.90,
    }


def test_prematch_full_history_without_model_separation_stays_wait():
    result = prematch_candidate(_neutral_features(), "TURKEY: Super Lig")
    assert result["state"] == "WAIT"
    assert result["score"] < 61.0


def test_prematch_candidate_is_evidence_led_not_history_led():
    result = prematch_candidate(_strong_home_features(), "TURKEY: Super Lig")
    assert result["state"] in {"PASS", "BORDERLINE"}
    assert result["mu_home"] > result["mu_away"]


def test_prematch_fair_probability_is_model_not_bookmaker():
    lane = {
        "scope": "FULL_MATCH",
        "market_family": "match_total",
        "line": 164.5,
        "probability": 0.52,
        "over": 1.82,
        "under": 2.02,
    }
    signal = prematch_signal(lane, _high_total_features(), "TURKEY: Super Lig")
    assert signal is not None
    assert signal["brain_mode"] == "basketball_prematch_v2"
    assert signal["direction"] == "over"
    assert signal["model_probability"] > signal["market_probability"]
    assert signal["fair_probability"] == signal["model_probability"]
    assert signal["strength"] <= 88.0


def test_prematch_quarter_scope_scales_game_mean_before_pricing():
    lane = {
        "scope": "QUARTER_1",
        "market_family": "match_total",
        "line": 40.5,
        "probability": 0.50,
        "over": 1.90,
        "under": 1.90,
    }
    signal = prematch_signal(lane, _high_total_features(), "TURKEY: Super Lig")
    assert signal is not None
    assert signal["scope_factor"] == 0.25
    assert 35.0 < signal["mu_total"] < 65.0


def test_recent_possessions_are_derived_from_attempts_rebounds_and_turnovers():
    first = {
        "stats_mode": "direct_segment",
        "segment_stats": {
            "offensive_rebounds": [1.0, 1.0],
            "turnovers": [1.0, 1.0],
        },
        "segment_attempts": {
            "field_goals": [5.0, 5.0],
            "free_throws": [1.0, 1.0],
        },
    }
    current = _direct_stats()
    result = recent_possession_metrics(first, current, 60.0)
    assert result["recent_possessions"] > 0
    assert result["recent_possessions_per_min"] > 0


def test_live_candidate_needs_multiple_snapshots_and_not_a_break():
    one = live_candidate_gate({
        "live_game_stats": {"current_segment_available": True},
        "history_points": 1,
        "recent_window_seconds": 60,
        "recent_score_rate": 5.0,
        "recent_possessions_per_min": 3.0,
        "break_transition": False,
    })
    assert one["state"] == "WAIT"

    paused = live_candidate_gate({
        "live_game_stats": {"current_segment_available": True},
        "history_points": 3,
        "recent_window_seconds": 60,
        "recent_score_rate": 5.0,
        "recent_possessions_per_min": 3.0,
        "break_transition": True,
    })
    assert paused["state"] == "WAIT"


def test_live_v2_uses_possession_pace_and_probability_edge_for_over():
    signal = live_signal(
        _brain(current=(10, 10), recent_poss=2.2, recent_score=4.0),
        _lane(score=(10, 10), elapsed=300, line=37.5, market_over=0.50),
    )
    assert signal is not None
    assert signal["direction"] == "over"
    assert signal["brain_mode"] == "basketball_live_v2"
    assert signal["possession_source"] == "flashscore_current_quarter"
    assert signal["model_probability"] > signal["market_probability"]
    assert signal["edge"] > 0.055
    assert signal["strength"] <= 88.0


def test_cumulative_box_score_is_not_treated_as_current_quarter_possessions():
    cumulative = {
        "current_segment_available": True,
        "stats_mode": "cumulative_through_current_segment",
        "segment_stats": {
            "field_goals": [30.0, 29.0],
            "three_point_field_goals": [10.0, 9.0],
            "free_throws": [14.0, 12.0],
            "offensive_rebounds": [12.0, 11.0],
            "turnovers": [14.0, 13.0],
            "fouls": [16.0, 15.0],
        },
        "segment_attempts": {
            "field_goals": [65.0, 63.0],
            "three_point_field_goals": [28.0, 27.0],
            "free_throws": [18.0, 17.0],
        },
    }
    signal = live_signal(
        _brain(current=(7, 8), recent_poss=1.3, recent_score=1.2, payload=cumulative),
        _lane(score=(7, 8), elapsed=300, line=40.5, market_over=0.65),
    )
    assert signal is not None
    assert signal["direction"] == "under"
    assert signal["possession_source"] == "flashscore_recent_delta"
    assert signal["model_probability"] < 0.97
    assert signal["probability_reliability"] < 0.90


def test_late_close_q4_under_is_blocked_for_intentional_foul_risk():
    payload = _direct_stats(low_efficiency=True)
    signal = live_signal(
        _brain(scope="QUARTER_4", current=(7, 7), recent_poss=1.6, recent_score=1.2, payload=payload),
        _lane(
            scope="QUARTER_4",
            elapsed=480,
            score=(7, 7),
            line=39.5,
            market_over=0.65,
            match_score=(84, 80),
        ),
    )
    assert signal is None
