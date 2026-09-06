from __future__ import annotations

from pathlib import Path

import pytest

from gool_bot2 import brain_v3_learning as learning


def _reset(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("RUNTIME_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("GOOL_BRAIN_V3_LEARNING_PATH", str(tmp_path / "learning.json"))
    monkeypatch.setenv("GOOL_BRAIN_V3_LEARNING_EVENTS_PATH", str(tmp_path / "events.jsonl"))
    monkeypatch.setenv("GOOL_BRAIN_V3_LEARNING", "1")
    monkeypatch.setenv("GOOL_BRAIN_V3_LEARN_MIN_GLOBAL", "30")
    monkeypatch.setenv("GOOL_BRAIN_V3_LEARN_MIN_PATTERN", "15")
    monkeypatch.setenv("GOOL_BRAIN_V3_LEARN_MAX_ADJUST_PP", "3.0")
    learning._STATE_CACHE = None
    learning._STATE_CACHE_PATH = None
    learning._BOOTSTRAPPED_JOURNALS.clear()


def _brain(probability: float = 0.80) -> dict:
    return {
        "version": 3,
        "active": True,
        "status": "BET",
        "strategy": "goal_before_ht",
        "period": "1H",
        "minute": 20,
        "score": [0, 0],
        "match_state": "HOME_PRESSURE",
        "pressure_index": 0.70,
        "home_pressure": 0.72,
        "away_pressure": 0.18,
        "home_trend": "RISING",
        "away_trend": "STEADY",
        "recent": {
            "xg5": 0.29,
            "xg10": 0.31,
            "xg15": 0.40,
            "xg5_source": "provider_xg",
            "xg10_source": "provider_xg",
            "shots5": 5.0,
            "sot5": 2.0,
            "big5": 0.0,
            "shots10": 7.0,
            "sot10": 3.0,
            "big10": 0.0,
        },
        "cumulative_xg_source": "provider_xg",
        "prematch": {"label": "supportive", "adjustment_pp": 1.0},
        "probability": probability,
        "confidence_score": probability * 100.0,
        "confidence_cap": 0.92,
        "bet_min": 0.70,
        "ready_min": 0.60,
        "live_foundation": True,
        "sustained_pressure": True,
        "blocks": [],
        "thoughts": [],
    }


def _row(index: int, outcome: str, probability: float = 0.80) -> dict:
    brain = _brain(probability)
    return {
        "entry_key": f"match-{index}:0-0:1H:0.5",
        "match_id": f"match-{index}",
        "minute": 20,
        "strategy": "goal_before_ht",
        "market_family": "first_half_total",
        "source": "brain_v3:state_machine",
        "signal_source": "GOOL_STATE",
        "probability": probability,
        "result": outcome,
        "experts": {
            "goal_before_ht": {
                "source": "brain_v3:state_machine",
                "diagnostics": {"brain_v3": brain},
            }
        },
        "stats_snapshot": {
            "xg": "0.00 : 0.37",
            "shots": "0 : 6",
            "sot": "0 : 2",
            "big_chances": "0 : 0",
        },
        "settled_stats_snapshot": {
            "xg": "0.00 : 0.40",
            "shots": "0 : 7",
            "sot": "0 : 2",
            "big_chances": "0 : 0",
        },
        "settled_minute": 45,
    }


def test_six_real_results_are_remembered_but_do_not_overfit(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _reset(tmp_path, monkeypatch)
    for index in range(6):
        learning.learn_settled_entry(_row(index, "won" if index % 2 == 0 else "lost"))

    summary = learning.learning_summary()
    assert summary["samples"] == 6
    assert summary["wins"] == 3
    assert summary["losses"] == 3
    assert summary["hit_rate"] == 0.5

    adjustment = learning.learning_adjustment(_brain())
    assert adjustment["active"] is False
    assert adjustment["reason"] == "collecting_samples"
    assert adjustment["adjustment_pp"] == 0.0


def test_loss_review_explains_pressure_dying_after_entry(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _reset(tmp_path, monkeypatch)
    review = learning.learn_settled_entry(_row(1, "lost"))
    assert review is not None
    assert "short_burst_overweighted" in review["diagnoses"]
    assert "pressure_died_after_entry" in review["diagnoses"]
    assert review["post_entry"]["shots_delta"] == pytest.approx(1.0)
    assert review["post_entry"]["sot_delta"] == pytest.approx(0.0)


def test_calibration_only_moves_after_mature_sample(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _reset(tmp_path, monkeypatch)
    # Same high-confidence pattern converts only 40% of the time. After 30
    # settled examples, learning may lower probability, but never by >3pp.
    for index in range(30):
        outcome = "won" if index < 12 else "lost"
        learning.learn_settled_entry(_row(index, outcome, probability=0.80))

    adjustment = learning.learning_adjustment(_brain(0.80))
    assert adjustment["active"] is True
    assert -3.0 <= adjustment["adjustment_pp"] < 0.0

    calibrated = learning.calibrate_brain_v3_decision(_brain(0.80))
    assert calibrated["probability"] < 0.80
    assert calibrated["probability"] >= 0.77
    assert calibrated["status"] == "BET"
    assert calibrated["learning"]["active"] is True


def test_settlement_correction_reverses_previous_learning(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _reset(tmp_path, monkeypatch)
    row = _row(4, "lost")
    learning.learn_settled_entry(row)
    row["result"] = "won"
    review = learning.learn_settled_entry(row)

    summary = learning.learning_summary()
    assert summary["samples"] == 1
    assert summary["wins"] == 1
    assert summary["losses"] == 0
    assert review is not None
    assert review["settlement_correction"] is True


def test_steam_is_never_used_to_train_brain_v3(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _reset(tmp_path, monkeypatch)
    row = _row(9, "lost")
    row["source"] = "1xbet:autonomous_steam"
    row["signal_source"] = "STEAM_OVERRIDE"
    assert learning.learn_settled_entry(row) is None
    assert learning.learning_summary()["samples"] == 0
