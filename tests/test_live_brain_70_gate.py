from __future__ import annotations

from gool_bot2.goal_state_policy import enforce_goal_state_policy
from gool_bot2.multi_public_metrics import ORDINARY_FORMULA, confidence_snapshot
from gool_bot2.multi_router import MarketCandidate, RouterDecision


def _candidate(*, pressure: float = 0.0, rating: float = 95.0) -> MarketCandidate:
    return MarketCandidate(
        key="first_half_total:2.5",
        family="first_half_total",
        label="1Т ТБ 2.5",
        odd=1.56,
        model_probability=0.95,
        correlation_key="any_next_goal",
        strategy="goal_before_ht",
        source="goal_state_engine:goal_before_ht",
        expert_passed=True,
        market_pressure_pp=pressure,
        market_override=True,
        value_override=True,
        market_age_seconds=5.0,
        data_quality=0.90,
        rating=rating,
        eligible=True,
    )


def _decision(row: MarketCandidate) -> RouterDecision:
    return RouterDecision(
        status="BET",
        minute=26,
        score=(2, 0),
        winner=row,
        alternatives=[],
        rejected=[],
        reason="legacy router",
    )


def test_live_goal_hazard_53_cannot_bypass_70_gate(monkeypatch):
    monkeypatch.setenv("GOOL_MULTI_MIN_RATING", "70")
    row = _candidate(pressure=9.0, rating=99.0)
    experts = {
        "goal_before_ht": {
            "probability": 0.53,
            "metric": "live_goal_hazard",
            "state": "PASS",
            "passed": True,
        }
    }

    final = enforce_goal_state_policy(_decision(row), experts)

    assert final.status == "WAIT"
    assert final.winner is None
    assert row.rating == 53.0
    assert row.model_probability == 0.53
    assert "goal_state_rating_below_70" in row.blocks
    assert row.market_override is False
    assert row.value_override is False


def test_live_brain_71_passes_independent_of_xbet_movement(monkeypatch):
    monkeypatch.setenv("GOOL_MULTI_MIN_RATING", "70")
    experts = {
        "goal_before_ht": {
            "probability": 0.71,
            "metric": "live_goal_hazard",
            "state": "PASS",
            "passed": True,
        }
    }

    positive = _candidate(pressure=9.0)
    negative = _candidate(pressure=-9.0)
    final_positive = enforce_goal_state_policy(_decision(positive), experts)
    final_negative = enforce_goal_state_policy(_decision(negative), experts)

    assert final_positive.status == "BET"
    assert final_negative.status == "BET"
    assert positive.rating == 71.0
    assert negative.rating == 71.0
    assert positive.market_override is False
    assert negative.market_override is False


def test_ordinary_public_confidence_is_exact_brain_v3_score():
    row = _candidate(pressure=12.0)
    experts = {
        "goal_before_ht": {
            "probability": 0.71,
            "metric": "brain_v3_probability",
            "source": "brain_v3:state_machine",
            "state": "PASS",
            "passed": True,
        }
    }
    record = {
        "live_momentum": {
            "xg_total_last_5m": 0.0,
            "sot_total_last_5m": 0.0,
            "shots_total_last_5m": 0.0,
        }
    }

    snap = confidence_snapshot(record, row, experts, data_quality=0.20)

    assert snap["layer"] == "GOOL"
    assert snap["confidence_score"] == 71.0
    assert snap["football_score"] == 71.0
    assert snap["formula"] == ORDINARY_FORMULA
    assert snap["formula"] == "Brain V3: LIVE multi-source + время/счёт + capped history/trends · 1xBet только кэф"
    assert snap["steam_confirmation"] is False
    assert snap["market_breadth_count"] == 0
