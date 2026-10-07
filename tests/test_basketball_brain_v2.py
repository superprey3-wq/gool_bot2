from gool_bot2.basketball_brain_v2 import (
    live_candidate_gate,
    live_quarter_context_assist,
    live_signal,
    prematch_candidate,
    prematch_signal,
    q3_rebound_assist,
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

    fresh = live_candidate_gate({
        "live_game_stats": {"current_segment_available": True},
        "history_points": 3,
        "recent_window_seconds": 60,
        "recent_score_rate": 4.0,
        "recent_possessions_per_min": 3.0,
        "break_transition": False,
    })
    assert fresh["state"] in {"PASS", "BORDERLINE"}


def test_live_candidate_allows_two_snapshots_after_real_activity():
    early = live_candidate_gate({
        "live_game_stats": {"current_segment_available": True},
        "history_points": 2,
        "recent_window_seconds": 30,
        "recent_score_rate": 5.0,
        "recent_possessions_per_min": 2.5,
        "recent_activity_available": True,
        "break_transition": False,
    })
    assert early["state"] in {"PASS", "BORDERLINE"}
    assert early["readiness_mode"] == "active_early"


def test_live_candidate_does_not_use_stale_two_snapshot_window():
    stale = live_candidate_gate({
        "live_game_stats": {"current_segment_available": True},
        "history_points": 2,
        "recent_window_seconds": 45,
        "recent_score_rate": 0.0,
        "recent_possessions_per_min": 0.0,
        "recent_activity_available": False,
        "break_transition": False,
    })
    assert stale["state"] == "WAIT"
    assert stale["readiness_mode"] == "warming"


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
    # 1.3 possessions/min without >=2 observed possessions is not enough to
    # trust Flashscore's bursty possession delta; use points/clock instead.
    assert signal["possession_source"] == "points_clock_fallback"
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


def test_q3_rebound_assist_detects_large_halftime_deficit_then_close_q3():
    brain = {
        "scope": "QUARTER_3",
        "score_parts": [[10, 20], [11, 21]],
        "elapsed_seconds": 300,
    }
    assist = q3_rebound_assist(
        brain,
        elapsed_seconds=300,
        current_segment_score=[18, 20],
    )
    assert assist["active"] is True
    assert assist["trailing_side"] == "home"
    assert assist["halftime_deficit"] == 20.0
    assert assist["q3_margin"] == -2.0
    assert assist["margin_improvement"] >= 8.0
    assert assist["stage"] == "close"
    assert assist["applied"] is False


def test_q3_rebound_assist_detects_true_q3_reversal():
    brain = {
        "scope": "QUARTER_3",
        "score_parts": [[21, 16], [30, 19]],
    }
    assist = q3_rebound_assist(
        brain,
        elapsed_seconds=360,
        current_segment_score=[17, 28],
    )
    assert assist["active"] is True
    assert assist["trailing_side"] == "away"
    assert assist["stage"] == "reversal"
    assert assist["q3_margin"] == 11.0


def test_q3_rebound_assist_does_not_trigger_when_q1_q2_winners_split():
    brain = {
        "scope": "QUARTER_3",
        "score_parts": [[24, 17], [22, 29]],
    }
    assist = q3_rebound_assist(
        brain,
        elapsed_seconds=300,
        current_segment_score=[26, 20],
    )
    assert assist["active"] is False
    assert assist["stage"] == "none"


def test_q3_assist_is_soft_and_only_adds_context_to_valid_signal():
    brain = _brain(current=(18, 20), recent_poss=2.2, recent_score=4.1)
    brain["score_parts"] = [[10, 20], [11, 21], [18, 20]]
    signal = live_signal(
        brain,
        _lane(score=(18, 20), elapsed=300, line=42.5, market_over=0.46),
    )
    assert signal is not None
    assist = signal["q3_rebound_assist"]
    assert assist["active"] is True
    assert assist["stage"] == "close"
    assert assist["applied"] is True


def test_q4_context_uses_lower_baseline_and_close_game_relief():
    profile = {"quarter_total": 41.5}
    close = live_quarter_context_assist(
        {
            "scope": "QUARTER_4",
            "score_parts": [[20, 18], [19, 21], [18, 17]],
        },
        profile,
    )
    comfortable = live_quarter_context_assist(
        {
            "scope": "QUARTER_4",
            "score_parts": [[25, 15], [23, 17], [22, 18]],
        },
        profile,
    )
    assert close["q4_baseline_adjustment"] < 0
    assert close["game_state_adjustment"] > 0
    assert comfortable["game_state_adjustment"] < 0
    assert close["adjusted_prior_total"] > comfortable["adjusted_prior_total"]


def test_hot_previous_quarter_softly_lowers_next_quarter_prior():
    profile = {"quarter_total": 41.5}
    context = live_quarter_context_assist(
        {
            "scope": "QUARTER_3",
            "score_parts": [[20, 20], [27, 25]],
        },
        profile,
    )
    assert context["previous_quarter_total"] == 52.0
    assert context["mean_reversion_adjustment"] < 0
    assert context["adjusted_prior_total"] < 41.5


def test_cold_previous_quarter_softly_raises_next_quarter_prior():
    profile = {"quarter_total": 41.5}
    context = live_quarter_context_assist(
        {
            "scope": "QUARTER_3",
            "score_parts": [[20, 20], [17, 16]],
        },
        profile,
    )
    assert context["previous_quarter_total"] == 33.0
    assert context["mean_reversion_adjustment"] > 0
    assert context["adjusted_prior_total"] > 41.5


def test_split_q1_q2_winner_pattern_is_diagnostic_only():
    profile = {"quarter_total": 41.5}
    context = live_quarter_context_assist(
        {
            "scope": "QUARTER_3",
            "score_parts": [[24, 18], [17, 22]],
        },
        profile,
    )
    diag = context["diagnostic_q3_split_winners"]
    assert diag["active"] is True
    assert diag["q1_winner"] == "home"
    assert diag["q2_winner"] == "away"
    assert diag["watch_side"] == "home"
    assert context["prior_adjustment"] == 0.0


def test_quarter_context_is_bounded_and_cannot_overpower_live_model():
    profile = {"quarter_total": 41.5}
    context = live_quarter_context_assist(
        {
            "scope": "QUARTER_4",
            "score_parts": [[40, 40], [40, 40], [60, 60]],
        },
        profile,
    )
    assert abs(context["prior_adjustment"]) <= 4.0
    assert 35.0 < context["adjusted_prior_total"] < 46.0


def test_live_points_pace_fallback_can_confirm_over_without_possession_attempts():
    payload = {
        "current_segment_available": True,
        "stats_mode": "direct_segment",
        "segment_stats": {},
        "segment_attempts": {},
    }
    brain = _brain(current=(13, 12), recent_poss=0.0, recent_score=5.2, payload=payload)
    brain["elapsed_seconds"] = 300.0
    signal = live_signal(
        brain,
        _lane(score=(13, 12), elapsed=300, line=39.5, market_over=0.50),
    )
    assert signal is not None
    assert signal["direction"] == "over"
    assert signal["possession_source"] == "points_clock_fallback"
    assert signal["directional_confirmation"] is True


def test_live_points_pace_fallback_stays_wait_when_direction_is_not_confirmed():
    payload = {
        "current_segment_available": True,
        "stats_mode": "direct_segment",
        "segment_stats": {},
        "segment_attempts": {},
    }
    brain = _brain(current=(11, 10), recent_poss=0.0, recent_score=4.1, payload=payload)
    brain["elapsed_seconds"] = 300.0
    signal = live_signal(
        brain,
        _lane(score=(11, 10), elapsed=300, line=39.5, market_over=0.50),
    )
    assert signal is None


def test_live_prefers_flashscore_quarter_clock_when_book_clock_is_invalid():
    brain = _brain(current=(10, 10), recent_poss=2.2, recent_score=4.0)
    brain["elapsed_seconds"] = 300.0
    signal = live_signal(
        brain,
        _lane(score=(10, 10), elapsed=600, line=37.5, market_over=0.50),
    )
    assert signal is not None
    assert signal["projection_clock_source"] == "flashscore_elapsed_fallback"


def test_live_prefers_bookmaker_game_clock_when_both_clocks_are_valid():
    brain = _brain(current=(10, 10), recent_poss=2.2, recent_score=4.0)
    brain["elapsed_seconds"] = 500.0
    signal = live_signal(
        brain,
        _lane(score=(10, 10), elapsed=300, line=37.5, market_over=0.50),
    )
    assert signal is not None
    assert signal["projection_clock_source"] == "1xbet_segment_clock"


def _full_match_memory():
    return {
        "sport": "basketball",
        "quality": 0.80,
        "expected_match_total": 160.0,
        "segments": {
            "QUARTER_1": {"expected_home": 20.0, "expected_away": 20.0, "expected_total": 40.0},
            "QUARTER_2": {"expected_home": 20.0, "expected_away": 20.0, "expected_total": 40.0},
            "QUARTER_3": {"expected_home": 20.0, "expected_away": 20.0, "expected_total": 40.0},
            "QUARTER_4": {"expected_home": 20.0, "expected_away": 20.0, "expected_total": 40.0},
        },
    }


def test_full_match_history_cannot_create_under_without_live_confirmation():
    payload = {
        "current_segment_available": True,
        "stats_mode": "cumulative_through_current_segment",
        "segment_stats": {},
        "segment_attempts": {},
    }
    brain = _brain(
        scope="QUARTER_2",
        current=(26, 23),
        recent_poss=0.0,
        recent_score=0.0,
        payload=payload,
    )
    brain["recent_activity_available"] = False
    brain["score"] = [53, 52]
    brain["segment_memory"] = _full_match_memory()
    lane = _lane(
        scope="FULL_MATCH",
        elapsed=580,
        score=(53, 52),
        line=210.5,
        market_over=0.50,
        match_score=(53, 52),
    )
    # The historical schedule likes UNDER here, but the current quarter is
    # actually running hot. History must not manufacture a LIVE direction.
    assert live_signal(brain, lane) is None


def test_full_match_under_survives_when_current_live_pace_confirms_it():
    payload = {
        "current_segment_available": True,
        "stats_mode": "cumulative_through_current_segment",
        "segment_stats": {},
        "segment_attempts": {},
    }
    brain = _brain(
        scope="QUARTER_2",
        current=(7, 8),
        recent_poss=0.0,
        recent_score=1.2,
        payload=payload,
    )
    brain["score"] = [45, 45]
    brain["segment_memory"] = _full_match_memory()
    signal = live_signal(
        brain,
        _lane(
            scope="FULL_MATCH",
            elapsed=580,
            score=(45, 45),
            line=190.5,
            market_over=0.50,
            match_score=(45, 45),
        ),
    )
    assert signal is not None
    assert signal["direction"] == "under"
    assert signal["directional_confirmation"] is True


def test_current_quarter_signal_still_waits_inside_final_35_seconds():
    brain = _brain(scope="QUARTER_2", current=(26, 23), recent_poss=1.7, recent_score=2.0)
    signal = live_signal(
        brain,
        _lane(
            scope="QUARTER_2",
            elapsed=580,
            score=(26, 23),
            line=55.5,
            market_over=0.50,
            match_score=(53, 52),
        ),
    )
    assert signal is None


def test_stale_tiny_recent_delta_does_not_confirm_under():
    payload = {
        "current_segment_available": True,
        "stats_mode": "cumulative_through_current_segment",
        "segment_stats": {},
        "segment_attempts": {},
    }
    brain = _brain(current=(8, 8), recent_poss=0.20, recent_score=0.0, payload=payload)
    brain["elapsed_seconds"] = 300.0
    brain["recent_possessions"] = 0.20
    brain["recent_activity_available"] = False

    signal = live_signal(
        brain,
        _lane(score=(8, 8), elapsed=300, line=39.5, market_over=0.50),
    )

    assert signal is None


def test_tiny_possession_delta_with_real_scoring_uses_points_clock_not_slow_possessions():
    payload = {
        "current_segment_available": True,
        "stats_mode": "cumulative_through_current_segment",
        "segment_stats": {},
        "segment_attempts": {},
    }
    brain = _brain(current=(13, 12), recent_poss=0.45, recent_score=5.2, payload=payload)
    brain["elapsed_seconds"] = 300.0
    brain["recent_possessions"] = 1.0
    brain["recent_activity_available"] = True

    signal = live_signal(
        brain,
        _lane(score=(13, 12), elapsed=300, line=39.5, market_over=0.50),
    )

    assert signal is not None
    assert signal["direction"] == "over"
    assert signal["possession_source"] == "points_clock_fallback"


def test_active_early_window_can_surface_over_but_not_under():
    payload = {
        "current_segment_available": True,
        "stats_mode": "cumulative_through_current_segment",
        "segment_stats": {},
        "segment_attempts": {},
    }

    early_under = _brain(current=(6, 6), recent_poss=0.0, recent_score=1.0, payload=payload)
    early_under.update({
        "history_points": 2,
        "recent_window_seconds": 30.0,
        "recent_activity_available": True,
        "elapsed_seconds": 300.0,
    })
    assert live_signal(
        early_under,
        _lane(score=(6, 6), elapsed=300, line=42.5, market_over=0.50),
    ) is None

    early_over = _brain(current=(14, 13), recent_poss=0.0, recent_score=6.0, payload=payload)
    early_over.update({
        "history_points": 2,
        "recent_window_seconds": 30.0,
        "recent_activity_available": True,
        "elapsed_seconds": 300.0,
    })
    signal = live_signal(
        early_over,
        _lane(score=(14, 13), elapsed=300, line=39.5, market_over=0.50),
    )
    assert signal is not None
    assert signal["direction"] == "over"
