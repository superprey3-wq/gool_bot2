from __future__ import annotations

from gool_bot2.brain_v3_decision_audit import (
    _reset_registry_for_tests,
    _under_countercase,
    audit_brain_v3_decision,
)


def _decision(**updates):
    row = {
        "version": 3,
        "active": True,
        "status": "BET",
        "strategy": "another_goal",
        "period": "2H",
        "minute": 67,
        "score": [1, 1],
        "probability": 0.78,
        "live_probability": 0.76,
        "confidence_score": 78.0,
        "confidence_cap": 0.92,
        "bet_min": 0.72,
        "ready_min": 0.60,
        "data_quality": 0.82,
        "match_state": "HOME_PRESSURE",
        "pressure_index": 0.72,
        "home_pressure": 0.72,
        "away_pressure": 0.35,
        "home_trend": "RISING",
        "away_trend": "STEADY",
        "recent": {
            "xg5": 0.30,
            "xg10": 0.52,
            "shots5": 5.0,
            "sot5": 2.0,
            "big5": 0.0,
        },
        "live_foundation": True,
        "sustained_pressure": True,
        "thoughts": [],
        "blocks": [],
    }
    row.update(updates)
    return row


def _record(mid: str):
    return {
        "match": {
            "flashscore_event_id": mid,
            "home": f"Home {mid}",
            "away": f"Away {mid}",
            "minute": 67,
            "home_score": 1,
            "away_score": 1,
        }
    }


def _experts(decision):
    return {
        decision["strategy"]: {
            "source": "brain_v3:state_machine",
            "probability": decision["probability"],
            "passed": True,
            "state": "PASS",
            "blocks": [],
            "diagnostics": {},
        }
    }


def test_under_countercase_blocks_calm_first_half_over():
    decision = _decision(
        strategy="goal_before_ht",
        period="1H",
        minute=33,
        match_state="CALM",
        pressure_index=0.35,
        home_trend="FALLING",
        away_trend="FALLING",
        recent={"xg5": 0.05, "xg10": 0.16, "shots5": 1.0, "sot5": 0.0, "big5": 0.0},
    )
    counter = _under_countercase(decision)
    assert counter["strong"] is True
    assert counter["no_more_goal_risk"] >= counter["threshold"]
    assert "match_calm" in counter["live_reasons"]


def test_strong_live_pressure_defeats_under_countercase():
    decision = _decision(
        match_state="END_TO_END",
        pressure_index=0.88,
        home_trend="RISING",
        away_trend="RISING",
        recent={"xg5": 0.38, "xg10": 0.64, "shots5": 7.0, "sot5": 3.0, "big5": 1.0},
    )
    counter = _under_countercase(decision)
    assert counter["strong"] is False
    assert counter["no_more_goal_risk"] < counter["threshold"]


def test_better_recent_match_blocks_weaker_bet():
    _reset_registry_for_tests()

    strong = _decision(
        probability=0.87,
        live_probability=0.85,
        data_quality=0.92,
        pressure_index=0.90,
        match_state="END_TO_END",
        home_trend="RISING",
        away_trend="RISING",
        recent={"xg5": 0.42, "xg10": 0.70, "shots5": 7.0, "sot5": 3.0, "big5": 1.0},
    )
    strong_out = audit_brain_v3_decision(_record("strong"), _experts(strong), strong)
    assert strong_out["status"] == "BET"

    weak = _decision(
        probability=0.74,
        live_probability=0.72,
        data_quality=0.70,
        pressure_index=0.66,
        match_state="HOME_PRESSURE",
        recent={"xg5": 0.22, "xg10": 0.40, "shots5": 4.0, "sot5": 2.0, "big5": 0.0},
    )
    experts = _experts(weak)
    weak_out = audit_brain_v3_decision(_record("weak"), experts, weak)
    assert weak_out["status"] == "READY"
    assert "brain_v3_better_candidate_available" in weak_out["blocks"]
    assert weak_out["selection_audit"]["better_candidate"]["match_id"] == "strong"
    assert experts["another_goal"]["passed"] is False
