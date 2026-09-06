from __future__ import annotations

from gool_bot2 import goal_state_engine as engine
from gool_bot2.live_goal_hazard import estimate_live_goal_hazard


def _side(confidence: float = 0.82) -> dict:
    return {
        "confidence_score": confidence,
        "pressure_score": 1.30,
        "evidence": 6,
        "recent_threat": True,
        "quality_threat": True,
        "prematch": {},
        "passed": True,
        "blocks": [],
    }


def _record(
    *,
    minute: int,
    score: tuple[int, int],
    xg: tuple[float, float],
    shots: tuple[float, float],
    sot: tuple[float, float],
    big: tuple[float, float],
    momentum: dict | None = None,
) -> dict:
    return {
        "match": {
            "flashscore_event_id": "hazard-test",
            "home": "Home",
            "away": "Away",
            "minute": minute,
            "home_score": score[0],
            "away_score": score[1],
            "is_halftime": False,
            "is_finished": False,
        },
        "providers": {
            "flashscore": {
                "stats": {
                    "xg": list(xg),
                    "shots": list(shots),
                    "shots_on_target": list(sot),
                    "big_chances": list(big),
                },
                "meta": {},
            }
        },
        "live_momentum": dict(momentum or {}),
        "cards": {},
    }


def test_late_low_quality_volume_is_vetoed(monkeypatch):
    states = {"home": _side(0.78), "away": _side(0.84)}
    monkeypatch.setattr(engine, "side_goal_pressure", lambda record, side: states[side])
    monkeypatch.setenv("GOOL_STATE_SIDE_PASS", "0.62")
    monkeypatch.setenv("GOOL_STATE_ANY_PASS", "0.64")

    # Mirrors the bad production shape: 73', 0:3, lots of shots/SOT but only
    # ~1.0 xG and no big chance. The old pressure score could PASS this.
    record = _record(
        minute=73,
        score=(0, 3),
        xg=(0.30, 0.69),
        shots=(4, 12),
        sot=(2, 5),
        big=(0, 0),
        momentum={
            "shots_total_last_10m": 4.0,
            "sot_total_last_10m": 2.0,
        },
    )

    hazard = estimate_live_goal_hazard(record, period="FT")
    experts = engine.build_goal_state_experts(record, model_result={}, data_quality=0.8)

    assert hazard["probability"] < hazard["minimum_probability"]
    assert hazard["late_quality_ok"] is False
    assert experts["another_goal"]["state"] == "BORDERLINE"
    assert experts["another_goal"]["passed"] is False
    assert "live_goal_hazard_veto" in experts["another_goal"]["blocks"]
    assert experts["another_goal"]["probability"] == hazard["probability"]


def test_late_genuine_quality_pressure_can_still_pass(monkeypatch):
    states = {"home": _side(0.84), "away": _side(0.80)}
    monkeypatch.setattr(engine, "side_goal_pressure", lambda record, side: states[side])
    monkeypatch.setenv("GOOL_STATE_SIDE_PASS", "0.62")
    monkeypatch.setenv("GOOL_STATE_ANY_PASS", "0.64")

    record = _record(
        minute=73,
        score=(1, 1),
        xg=(1.15, 1.20),
        shots=(11, 12),
        sot=(4, 5),
        big=(1, 1),
        momentum={
            "xg_total_last_5m": 0.25,
            "xg_total_last_10m": 0.50,
            "shots_total_last_5m": 4.0,
            "sot_total_last_5m": 2.0,
            "shots_total_last_10m": 7.0,
            "sot_total_last_10m": 3.0,
        },
    )

    hazard = estimate_live_goal_hazard(record, period="FT")
    experts = engine.build_goal_state_experts(record, model_result={}, data_quality=0.9)

    assert hazard["probability"] >= hazard["minimum_probability"]
    assert hazard["late_quality_ok"] is True
    assert experts["another_goal"]["state"] == "PASS"
    assert experts["another_goal"]["passed"] is True
    assert experts["another_goal"]["metric"] == "live_goal_hazard"
    assert experts["another_goal"]["probability"] == hazard["probability"]


def test_late_first_half_also_needs_remaining_goal_hazard(monkeypatch):
    states = {"home": _side(0.86), "away": _side(0.82)}
    monkeypatch.setattr(engine, "side_goal_pressure", lambda record, side: states[side])
    monkeypatch.setenv("GOOL_STATE_SIDE_PASS", "0.62")
    monkeypatch.setenv("GOOL_STATE_ANY_PASS", "0.64")
    monkeypatch.setenv("GOOL_STATE_FIRST_HALF_PASS", "0.64")

    record = _record(
        minute=34,
        score=(0, 0),
        xg=(0.35, 0.35),
        shots=(5, 5),
        sot=(1, 1),
        big=(0, 0),
        momentum={
            "shots_total_last_10m": 2.0,
            "sot_total_last_10m": 0.0,
        },
    )

    hazard = estimate_live_goal_hazard(record, period="1H")
    experts = engine.build_goal_state_experts(record, model_result={}, data_quality=0.8)

    assert hazard["probability"] < hazard["minimum_probability"]
    assert experts["goal_before_ht"]["state"] == "BORDERLINE"
    assert experts["goal_before_ht"]["passed"] is False
    assert "live_goal_hazard_veto" in experts["goal_before_ht"]["blocks"]
