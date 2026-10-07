from gool_bot2.hockey_brain_v2 import (
    _side_probabilities,
    live_candidate_gate,
    live_signal,
    prematch_candidate,
    prematch_signal,
)


def _strong_features():
    return {
        "home_recent_n": 10,
        "away_recent_n": 10,
        "home_gf_avg": 3.7,
        "home_ga_avg": 1.9,
        "away_gf_avg": 2.1,
        "away_ga_avg": 3.5,
        "home_venue_gf_avg": 3.9,
        "home_venue_ga_avg": 1.8,
        "away_venue_gf_avg": 1.9,
        "away_venue_ga_avg": 3.7,
        "recent_total_avg": 5.6,
        "venue_total_avg": 5.8,
        "h2h_total_avg": 5.0,
        "home_rest_days": 2.0,
        "away_rest_days": 1.0,
        "rest_advantage_days": 1.0,
    }


def _live_brain(*, scope="PERIOD_2", recent_shot_rate=2.6, match_score=(1, 1), shot_rate_available=True):
    return {
        "scope": scope,
        "history_points": 3,
        "recent_window_seconds": 60.0,
        "recent_shot_rate": recent_shot_rate,
        "shot_rate_available": shot_rate_available,
        "league": "KHL",
        "live_game_stats": {
            "current_segment_available": True,
            "stats_mode": "cumulative_through_current_segment",
            "segment_stats": {
                # Deliberately huge cumulative shots: v2 must not treat these
                # as current-period pace.
                "shots_on_goal": [50, 50],
                "penalties_2m": [0, 0],
                "powerplay_goals": [0, 0],
            },
        },
        "score": list(match_score),
        "current_segment_score": [0, 0],
        "prematch_match_lambda": 5.05,
    }


def _live_lane(*, scope="PERIOD_2", elapsed=600, line=0.5, market_over=0.50, match_score=(1, 1)):
    return {
        "scope": scope,
        "market_family": "match_total",
        "clock_seconds": elapsed,
        "line": line,
        "score": [0, 0],
        "match_score": list(match_score),
        "league": "KHL",
        "probability": market_over,
        "over": 1.80,
        "under": 2.00,
    }


def test_prematch_candidate_rejects_thin_history_instead_of_auto_shortlisting():
    features = {
        "home_recent_n": 2,
        "away_recent_n": 2,
        "home_gf_avg": 5.0,
        "home_ga_avg": 1.0,
        "away_gf_avg": 1.0,
        "away_ga_avg": 5.0,
    }
    result = prematch_candidate(features, "KHL")
    assert result["state"] == "WAIT"
    assert result["data_quality"] < 0.46


def test_prematch_model_probability_is_not_bookmaker_probability():
    lane = {
        "market_family": "moneyline",
        "selection_side": "home",
        "choice_key": "home",
        "line": 0.0,
        "odd": 1.75,
        "probability": 0.58,
        "selection": "П1",
    }
    signal = prematch_signal(lane, _strong_features(), "KHL")
    assert signal is not None
    assert signal["brain_mode"] == "hockey_prematch_v2"
    assert signal["model_probability"] > signal["market_probability"]
    assert signal["fair_probability"] == signal["model_probability"]
    assert signal["edge"] > 0.06
    assert signal["strength"] <= 89.0


def test_integer_total_tracks_push_separately():
    win, push, loss = _side_probabilities(1.8, 1.0, "over")
    assert 0.0 < push < 1.0
    assert abs((win + push + loss) - 1.0) < 1e-9


def test_live_gate_requires_fresh_segment_window_not_just_two_snapshots():
    first = live_candidate_gate({
        "live_game_stats": {"current_segment_available": True},
        "history_points": 1,
        "recent_window_seconds": 0.0,
        "recent_shot_rate": 4.0,
    })
    assert first["state"] == "WAIT"

    too_early = live_candidate_gate({
        "live_game_stats": {"current_segment_available": True},
        "history_points": 2,
        "recent_window_seconds": 25.0,
        "recent_shot_rate": 2.6,
    })
    assert too_early["state"] == "WAIT"

    fresh = live_candidate_gate({
        "live_game_stats": {"current_segment_available": True},
        "history_points": 3,
        "recent_window_seconds": 60.0,
        "recent_shot_rate": 1.1,
    })
    assert fresh["state"] in {"PASS", "BORDERLINE"}


def test_live_fast_recent_pressure_can_emit_over_without_using_cumulative_shot_total():
    signal = live_signal(
        _live_brain(recent_shot_rate=2.6),
        _live_lane(line=0.5, market_over=0.50),
    )
    assert signal is not None
    assert signal["direction"] == "over"
    assert signal["brain_mode"] == "hockey_live_v2"
    assert signal["edge"] > 0.055
    assert signal["strength"] <= 89.0


def test_normal_combined_sog_pace_does_not_auto_create_an_under():
    # KHL baseline is roughly around this combined SOG/min range in the model.
    # A normal pace should be neutral evidence, not an automatic UNDER trigger.
    signal = live_signal(
        _live_brain(recent_shot_rate=1.05),
        _live_lane(line=1.5, market_over=0.50),
    )
    assert signal is None


def test_huge_cumulative_shots_do_not_force_over_when_recent_pressure_is_low():
    signal = live_signal(
        _live_brain(recent_shot_rate=0.5),
        _live_lane(line=1.5, market_over=0.70),
    )
    assert signal is not None
    assert signal["direction"] == "under"
    assert signal["shot_factor"] < 1.0


def test_late_close_p3_under_is_blocked_for_empty_net_risk():
    signal = live_signal(
        _live_brain(scope="PERIOD_3", recent_shot_rate=0.5, match_score=(3, 2)),
        _live_lane(
            scope="PERIOD_3",
            elapsed=900,
            line=1.5,
            market_over=0.70,
            match_score=(3, 2),
        ),
    )
    assert signal is None


def test_late_big_lead_p3_under_can_still_be_evaluated():
    signal = live_signal(
        _live_brain(scope="PERIOD_3", recent_shot_rate=0.5, match_score=(5, 1)),
        _live_lane(
            scope="PERIOD_3",
            elapsed=900,
            line=1.5,
            market_over=0.70,
            match_score=(5, 1),
        ),
    )
    assert signal is not None
    assert signal["direction"] == "under"


def test_live_uses_flashscore_period_clock_when_bookmaker_clock_is_cumulative():
    brain = _live_brain(recent_shot_rate=0.5)
    brain["elapsed_seconds"] = 600.0
    brain["recent_window_seconds"] = 60.0
    brain["segment_score_verified"] = True
    lane = _live_lane(elapsed=2400, line=1.5, market_over=0.70)

    signal = live_signal(brain, lane)

    assert signal is not None
    assert signal["direction"] == "under"
    assert signal["projection_clock_source"] == "flashscore_period"


def test_live_uses_bookmaker_period_score_when_flashscore_parts_are_incomplete():
    brain = _live_brain(recent_shot_rate=0.5)
    brain["elapsed_seconds"] = 600.0
    brain["recent_window_seconds"] = 60.0
    brain["segment_score_verified"] = False
    # The unverified Flashscore fallback incorrectly looks like two P3 goals,
    # while the bookmaker subgame correctly says 0:0.
    brain["current_segment_score"] = [2, 0]
    lane = _live_lane(elapsed=600, line=1.5, market_over=0.70)
    lane["score"] = [0, 0]

    signal = live_signal(brain, lane)

    assert signal is not None
    assert signal["direction"] == "under"
    assert signal["segment_score_source"] == "1xbet_subgame_fallback"


def test_full_quality_without_model_separation_stays_out_of_prematch_shortlist():
    features = {
        "home_recent_n": 10,
        "away_recent_n": 10,
        "home_gf_avg": 2.525,
        "home_ga_avg": 2.525,
        "away_gf_avg": 2.525,
        "away_ga_avg": 2.525,
        "home_venue_gf_avg": 2.525,
        "home_venue_ga_avg": 2.525,
        "away_venue_gf_avg": 2.525,
        "away_venue_ga_avg": 2.525,
        "recent_total_avg": 5.05,
        "venue_total_avg": 5.05,
        "h2h_total_avg": 5.05,
        "home_rest_days": 1.0,
        "away_rest_days": 1.0,
        "rest_advantage_days": 0.0,
    }
    result = prematch_candidate(features, "KHL")
    assert result["data_quality"] >= 0.99
    assert result["state"] == "WAIT"


def test_prematch_period_total_uses_period_lambda_not_full_match_lambda():
    features = _strong_features()
    lane = {
        "scope": "PERIOD_1",
        "market_family": "match_total",
        "line": 1.5,
        "over": 1.90,
        "under": 1.90,
        "probability": 0.50,
    }
    signal = prematch_signal(lane, features, "NHL")
    assert signal is not None
    assert signal["scope_goal_share"] == 0.34
    assert signal["lambda_home"] < signal["full_match_lambda_home"]
    assert signal["lambda_away"] < signal["full_match_lambda_away"]
    # The old bug produced ~0.98 OVER by feeding a full-match lambda into P1.
    assert signal["model_probability"] < 0.85
    assert signal["strength"] <= 87.0


def test_live_short_pressure_window_does_not_count_as_shot_confirmation():
    brain = _live_brain(recent_shot_rate=0.0)
    brain["elapsed_seconds"] = 600.0
    brain["recent_window_seconds"] = 30.0
    brain["segment_score_verified"] = True
    lane = _live_lane(elapsed=600, line=1.5, market_over=0.70)

    assert live_signal(brain, lane) is None


def test_prematch_respects_shared_minimum_odd(monkeypatch):
    monkeypatch.setenv("GOOL_MULTISPORT_MIN_ODD", "1.45")
    lane = {
        "scope": "FULL_MATCH",
        "market_family": "handicap",
        "selection_side": "away",
        "choice_key": "away",
        "line": 2.5,
        "odd": 1.38,
        "probability": 0.66,
        "selection": "Ф2 +2.5",
    }
    assert prematch_signal(lane, _strong_features(), "NHL") is None


def test_live_integer_line_push_reduces_displayed_confidence():
    brain = _live_brain(recent_shot_rate=0.5)
    brain["elapsed_seconds"] = 600.0
    brain["segment_score_verified"] = True
    # Keep this as a genuinely strong integer-line UNDER so the test checks
    # push handling without reviving the weak 0.85-vs-1.0 signal class.
    lane = _live_lane(elapsed=600, line=2.0, market_over=0.70)

    signal = live_signal(brain, lane)

    assert signal is not None
    assert signal["direction"] == "under"
    assert signal["push_probability"] > 0.0
    assert signal["push_confidence_penalty"] > 0.0
    assert signal["strength"] < 87.0


def test_missing_sog_is_not_treated_as_zero_pace_under_confirmation():
    signal = live_signal(
        _live_brain(recent_shot_rate=0.0, shot_rate_available=False),
        _live_lane(line=1.5, market_over=0.70),
    )
    assert signal is None


def test_hockey_segment_rejects_tiny_stat_edge_like_under_one_at_projection_point_85():
    signal = live_signal(
        _live_brain(recent_shot_rate=0.45, shot_rate_available=True),
        _live_lane(line=1.0, market_over=0.65),
    )
    assert signal is None


def test_hockey_under_requires_real_current_period_sog():
    signal = live_signal(
        _live_brain(recent_shot_rate=0.0, shot_rate_available=False),
        _live_lane(line=1.5, market_over=0.70),
    )
    assert signal is None
