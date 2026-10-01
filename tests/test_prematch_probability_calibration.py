from __future__ import annotations

from gool_bot2.prematch_probability_calibration import calibrate_probability


def profile(sample=6):
    return {
        "full_match": {"pair_sample": sample},
        "first_half": {"pair_sample": sample},
        "second_half": {"pair_sample": sample},
    }


def settled_rows(*, wins: int, losses: int, probability: float = 0.65):
    rows = []
    for i in range(wins + losses):
        rows.append({
            "origin": "prematch",
            "scope": "FULL_TIME",
            "market_type": "OVER_UNDER",
            "probability": probability,
            "result": "won" if i < wins else "lost",
        })
    return rows


def test_raw_model_probability_is_not_shown_directly_without_history():
    result = calibrate_probability(
        raw_model_probability=0.85,
        market_probability=0.55,
        profile=profile(4),
        scope="FULL_TIME",
        market_type="OVER_UNDER",
        quality=1.0,
        rows=[],
    )
    assert 0.55 < result["honest_probability"] < 0.70
    assert result["calibration_sample"] == 0
    assert result["confidence"] == "LOW"


def test_bad_settled_history_pulls_probability_down():
    no_history = calibrate_probability(
        raw_model_probability=0.78,
        market_probability=0.60,
        profile=profile(10),
        scope="FULL_TIME",
        market_type="OVER_UNDER",
        quality=1.0,
        rows=[],
    )
    calibrated = calibrate_probability(
        raw_model_probability=0.78,
        market_probability=0.60,
        profile=profile(10),
        scope="FULL_TIME",
        market_type="OVER_UNDER",
        quality=1.0,
        rows=settled_rows(wins=18, losses=32, probability=0.65),
    )
    assert calibrated["calibration_sample"] == 50
    assert calibrated["historical_hit_rate"] == 0.36
    assert calibrated["honest_probability"] < no_history["honest_probability"]
    assert calibrated["confidence"] == "MEDIUM"


def test_large_good_history_can_support_high_confidence_probability():
    calibrated = calibrate_probability(
        raw_model_probability=0.82,
        market_probability=0.70,
        profile=profile(14),
        scope="FULL_TIME",
        market_type="OVER_UNDER",
        quality=1.0,
        rows=settled_rows(wins=68, losses=17, probability=0.75),
    )
    assert calibrated["calibration_sample"] == 85
    assert calibrated["confidence"] == "HIGH"
    assert calibrated["honest_probability"] > 0.70
    assert calibrated["range_low"] < calibrated["honest_probability"] < calibrated["range_high"]


def test_history_is_market_family_specific():
    rows = settled_rows(wins=20, losses=0, probability=0.65)
    result = calibrate_probability(
        raw_model_probability=0.70,
        market_probability=0.60,
        profile=profile(10),
        scope="FIRST_HALF",
        market_type="BOTH_TEAMS_TO_SCORE",
        quality=1.0,
        rows=rows,
    )
    assert result["calibration_sample"] == 0
    assert result["confidence"] == "LOW"
