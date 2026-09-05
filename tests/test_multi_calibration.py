from __future__ import annotations

from gool_bot2.multi_calibration import calibration_summary


def test_calibration_summary_compares_predicted_to_actual_and_market():
    rows = [
        {
            "strategy": "goal_before_ht",
            "result": "won",
            "probability": 0.78,
            "market_probability": 0.64,
            "calibration": {
                "predicted_probability": 0.78,
                "market_fair_probability": 0.64,
                "probability_bucket": "75-80%",
            },
        },
        {
            "strategy": "goal_before_ht",
            "result": "lost",
            "probability": 0.76,
            "market_probability": 0.63,
            "calibration": {
                "predicted_probability": 0.76,
                "market_fair_probability": 0.63,
                "probability_bucket": "75-80%",
            },
        },
    ]
    out = calibration_summary(rows)
    assert out["settled_sample"] == 2
    assert out["overall"]["predicted"] == 0.77
    assert out["overall"]["actual"] == 0.5
    assert out["overall"]["calibration_error_pp"] == -27.0
    assert out["overall"]["model_vs_market_pp"] == 13.5
    assert out["production_effect"] == "diagnostic_only"


def test_calibration_ignores_pending_and_void_rows():
    rows = [
        {"strategy": "another_goal", "result": "pending", "probability": 0.80},
        {"strategy": "another_goal", "result": "void", "probability": 0.80},
    ]
    out = calibration_summary(rows)
    assert out["settled_sample"] == 0
    assert out["overall"] is None
