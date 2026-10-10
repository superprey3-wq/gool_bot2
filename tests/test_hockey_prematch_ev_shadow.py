from __future__ import annotations

import math

from gool_bot2.hockey_prematch_ev_shadow import evaluate_hockey_prematch_ev_shadow


def _features():
    return {
        "home_recent_n": 10, "away_recent_n": 10,
        "home_gf_avg": 3.3, "home_ga_avg": 2.7,
        "away_gf_avg": 3.0, "away_ga_avg": 2.8,
        "home_venue_gf_avg": 3.4, "away_venue_gf_avg": 3.0,
        "home_venue_ga_avg": 2.6, "away_venue_ga_avg": 3.1,
        "recent_total_avg": 6.0, "venue_total_avg": 6.1,
        "home_rest_days": 2, "away_rest_days": 2,
    }


def _lane():
    return {
        "scope": "FULL_MATCH", "market_family": "match_total",
        "line": 6.0, "over": 2.15, "under": 1.75,
        "probability": .47,
    }


def test_ev_uses_raw_win_push_loss_not_conditional_probability():
    result = evaluate_hockey_prematch_ev_shadow(
        _lane(), _features(), "NHL", active_signal={"direction": "over"}
    )
    assert result["model"] == "hockey_prematch_ev_shadow"
    assert result["probabilities_calibrated"] is False
    assert len(result["alternatives"]) == 2
    assert result["active_v2_direction"] == "over"
    for candidate in result["alternatives"]:
        w, p, l = (
            candidate["win_probability_uncalibrated"],
            candidate["push_probability"],
            candidate["loss_probability_uncalibrated"],
        )
        assert math.isclose(w + p + l, 1.0, abs_tol=3e-6)
        assert p > 0.01  # Integer goal line can push
        true_ev = w * (candidate["odd"] - 1) - l
        assert math.isclose(candidate["expected_value_units_uncalibrated"], true_ev, abs_tol=3e-6)
        assert math.isclose(candidate["conditional_win_probability_uncalibrated"], w / (w + l), abs_tol=3e-6)


def test_low_quality_and_unpriced_markets_are_wait():
    weak = {**_features(), "home_recent_n": 0, "away_recent_n": 0}
    assert evaluate_hockey_prematch_ev_shadow(
        _lane(), weak, "NHL"
    )["reason"] == "insufficient_team_data"
    no_price = {**_lane(), "over": float("nan"), "under": 1.39}
    assert evaluate_hockey_prematch_ev_shadow(
        no_price, _features(), "NHL"
    )["reason"] == "no_eligible_price"


def test_period_shadow_and_invalid_scope():
    period = {**_lane(), "scope": "PERIOD_2", "line": 2.5}
    result = evaluate_hockey_prematch_ev_shadow(period, _features(), "NHL")
    assert result["scope"] == "PERIOD_2"
    assert result["lambda_scoped"] < 4.0
    assert evaluate_hockey_prematch_ev_shadow(
        {**period, "scope": "OVERTIME"}, _features(), "NHL"
    )["reason"] == "unknown_goals_scope"


def test_not_a_hockey_moneyline_prediction_and_not_sent():
    result = evaluate_hockey_prematch_ev_shadow(
        {**_lane(), "market_family": "moneyline"}, _features(), "NHL"
    )
    assert result["status"] == "WAIT"
    assert result["reason"] == "only_scoped_goals_totals_supported"
    assert "telegram_sent" not in result
