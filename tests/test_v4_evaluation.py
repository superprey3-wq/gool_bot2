from gool_bot2.v4_evaluation import (
    PredictionSnapshot, brier_binary, closing_line_value,
    expected_calibration_error, log_loss_binary,
)
from gool_bot2.v4_prematch_engine import PrematchPick


def test_snapshot_preserves_pre_match_price_and_edge():
    pick = PrematchPick("m1", "A", "B", "match_total", "over 2.5", 2.0, 0.60, 0.52, 0.9)
    row = PredictionSnapshot.from_pick(pick, snapshot_id="m1:over25")
    assert row.odds == 2.0
    assert abs(row.edge - 0.08) < 1e-9
    assert row.model_version == "v4"


def test_calibration_metrics_reward_good_probabilities():
    probs = [0.9, 0.8, 0.2, 0.1]
    outcomes = [1, 1, 0, 0]
    assert brier_binary(probs, outcomes) < 0.05
    assert log_loss_binary(probs, outcomes) < 0.25
    assert expected_calibration_error(probs, outcomes, bins=5) < 0.2


def test_clv_is_positive_when_taken_price_beats_close():
    assert closing_line_value(2.0, 0.55) > 0
