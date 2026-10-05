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


def _live_brain(*, scope="PERIOD_2", recent_shot_rate=2.6, match_score=(1, 1)):
    return {
        "scope": scope,
        "history_points": 3,
        "recent_shot_rate": recent_shot_rate,
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


def test_live_gate_requires_fresh_second_snapshot():
    first = live_candidate_gate({
        "live_game_stats": {"current_segment_available": True},
        "history_points": 1,
        "recent_shot_rate": 4.0,
    })
    assert first["state"] == "WAIT"

    second = live_candidate_gate({
        "live_game_stats": {"current_segment_available": True},
        "history_points": 2,
        "recent_shot_rate": 2.6,
    })
    assert second["state"] in {"PASS", "BORDERLINE"}


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
