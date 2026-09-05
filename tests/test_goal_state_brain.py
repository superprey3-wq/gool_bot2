from __future__ import annotations

from gool_bot2 import goal_state_engine as engine
from gool_bot2.goal_state_policy import enforce_goal_state_policy
from gool_bot2.multi_router import MarketCandidate, RouterDecision


def _side(*, confidence: float, passed: bool, recent: bool = True, quality: bool = True) -> dict:
    return {
        "confidence_score": confidence,
        "pressure_score": 1.25,
        "evidence": 6,
        "recent_threat": recent,
        "quality_threat": quality,
        "prematch": {"scored_rate": 0.75, "avg_goals_for": 1.45},
        "passed": passed,
        "blocks": [] if passed else ["side_pressure_low"],
    }


def test_first_half_goal_keeps_thinking_after_25(monkeypatch):
    states = {
        "home": _side(confidence=0.82, passed=True),
        "away": _side(confidence=0.76, passed=True),
    }
    monkeypatch.setattr(engine, "side_goal_pressure", lambda record, side: states[side])
    record = {
        "match": {"minute": 34, "home_score": 0, "away_score": 0, "is_halftime": False},
        "cards": {},
    }
    model = {
        "trained_probability": {"another_goal": 0.80, "goal_before_ht": 0.76},
        "another_goal_live": {"combined_pressure": 1.35, "passed": True},
    }

    experts = engine.build_goal_state_experts(record, model_result=model, data_quality=0.9)

    assert experts["goal_before_ht"]["state"] in {"PASS", "BORDERLINE"}
    assert experts["goal_before_ht"]["probability"] > 0.60
    assert experts["goal_before_ht"]["source"] == "goal_state_engine:goal_before_ht"


def test_btts_at_one_zero_is_driven_by_scoreless_side(monkeypatch):
    states = {
        "home": _side(confidence=0.84, passed=True),
        "away": _side(confidence=0.48, passed=False, recent=False, quality=False),
    }
    monkeypatch.setattr(engine, "side_goal_pressure", lambda record, side: states[side])
    record = {
        "match": {"minute": 55, "home_score": 1, "away_score": 0},
        "cards": {},
    }

    experts = engine.build_goal_state_experts(record, model_result={}, data_quality=0.9)

    assert experts["btts"]["diagnostics"]["target_side"] == "away"
    assert experts["btts"]["state"] == "HARD_NO"
    assert "expert_hard_no" in experts["btts"]["blocks"]


def _candidate(*, odd: float = 1.80, pressure: float = 0.0, override: bool = False) -> MarketCandidate:
    return MarketCandidate(
        key="match_total:0.5",
        family="match_total",
        label="ТБ 0.5",
        odd=odd,
        model_probability=0.78,
        correlation_key="any_next_goal",
        strategy="another_goal",
        source="goal_state_engine:another_goal",
        expert_passed=False,
        market_pressure_pp=pressure,
        market_override=override,
        market_age_seconds=5.0,
        data_quality=0.9,
        rating=80.0,
        eligible=True,
    )


def test_hard_no_cannot_be_revived_by_normal_market_override():
    row = _candidate(pressure=9.0, override=True)
    decision = RouterDecision(
        status="BET",
        minute=50,
        score=(0, 0),
        winner=row,
        alternatives=[],
        rejected=[],
        reason="legacy",
    )
    experts = {
        "another_goal": {
            "probability": 0.78,
            "metric": "confidence",
            "state": "HARD_NO",
            "passed": False,
        }
    }

    final = enforce_goal_state_policy(decision, experts)

    assert final.status == "WAIT"
    assert final.winner is None
    assert "goal_state_hard_no" in row.blocks


def test_borderline_needs_market_confirmation_and_min_odd_150():
    row = _candidate(odd=1.50, pressure=7.0, override=True)
    decision = RouterDecision(
        status="BET",
        minute=50,
        score=(0, 0),
        winner=row,
        alternatives=[],
        rejected=[],
        reason="legacy",
    )
    experts = {
        "another_goal": {
            "probability": 0.74,
            "metric": "confidence",
            "state": "BORDERLINE",
            "passed": False,
        }
    }

    final = enforce_goal_state_policy(decision, experts)

    assert final.status == "BET"
    assert final.winner is row
    assert row.odd == 1.50
    assert row.expected_roi == 0.0
    assert row.value_edge_pp == 0.0

    low = _candidate(odd=1.49, pressure=9.0, override=True)
    low_decision = RouterDecision(
        status="BET",
        minute=50,
        score=(0, 0),
        winner=low,
        alternatives=[],
        rejected=[],
        reason="legacy",
    )
    low_final = enforce_goal_state_policy(low_decision, experts)
    assert low_final.status == "WAIT"
    assert "price_too_low" in low.blocks
