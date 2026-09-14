from __future__ import annotations

from gool_bot2.xbet_multisport_steam import (
    SPORTS,
    _balanced_total,
    _metric,
    _score,
    detect_steam,
)


def test_score_reads_livefeed_final_score_shape():
    game = {"SC": {"FS": {"S1": 3, "S2": 2}}}
    assert _score(game) == (3, 2)


def test_balanced_total_uses_main_total_pair():
    game = {
        "GE": [
            {
                "E": [
                    [
                        {"G": 4, "T": 9, "P": 5.5, "C": 1.82},
                        {"G": 4, "T": 10, "P": 5.5, "C": 2.02},
                        {"G": 4, "T": 9, "P": 6.5, "C": 2.55},
                        {"G": 4, "T": 10, "P": 6.5, "C": 1.48},
                    ]
                ]
            }
        ]
    }
    row = _balanced_total(game)
    assert row is not None
    assert row["line"] == 5.5
    assert 0.50 < row["probability"] < 0.55


def test_remaining_total_metric_does_not_jump_just_because_score_changed():
    cfg = SPORTS["hockey"]
    before = {"line": 6.5, "probability": 0.50}
    after = {"line": 7.5, "probability": 0.50}
    assert _metric(before, (1, 1), cfg) == _metric(after, (2, 1), cfg)


def _rows(metrics, *, step=10.0, line=5.5, probability=0.52, over=1.82):
    return [
        {
            "ts": idx * step,
            "metric": metric,
            "probability": probability + idx * 0.01,
            "line": line + idx * 0.1,
            "over": over - idx * 0.03,
        }
        for idx, metric in enumerate(metrics)
    ]


def test_hockey_strong_future_total_pressure_emits_signal():
    cfg = SPORTS["hockey"]
    rows = _rows([4.50, 4.66, 4.84, 5.05])
    signal = detect_steam(rows, cfg, now=30.0, score_changed_at=None)
    assert signal is not None
    assert signal["metric_delta"] >= cfg.min_metric_delta
    assert signal["moves"] >= cfg.min_moves


def test_hockey_recent_goal_repricing_is_guarded():
    cfg = SPORTS["hockey"]
    rows = _rows([4.50, 4.66, 4.84, 5.05])
    assert detect_steam(rows, cfg, now=30.0, score_changed_at=20.0) is None


def test_basketball_requires_material_remaining_total_shift():
    cfg = SPORTS["basketball"]
    weak = _rows([92.0, 92.5, 93.0, 93.4])
    strong = _rows([92.0, 93.2, 94.4, 96.0])
    assert detect_steam(weak, cfg, now=30.0, score_changed_at=None) is None
    signal = detect_steam(strong, cfg, now=30.0, score_changed_at=None)
    assert signal is not None
    assert signal["metric_delta"] >= cfg.min_metric_delta
