from __future__ import annotations

import pytest

from gool_bot2 import multi_runtime as runtime


def test_live_only_brain_strips_prematch_without_deleting_diagnostics(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("GOOL_LIVE_ONLY", raising=False)
    record = {
        "match": {"flashscore_event_id": "m1", "minute": 52},
        "prematch_context": {"home_recent": [{"home": "A"}]},
        "prematch_goal_profile": {"active": {"available": True}},
        "xbet_prematch_market": {"available": True},
        "live_momentum": {"shots_total_last_5m": 3},
    }
    model = {
        "trained_probability": {"another_goal": 0.88},
        "direct": {"goal_before_ht": 0.77},
        "another_goal_live": {"combined_pressure": 1.25, "passed": True},
    }
    two_more = {"confidence_score": 0.91, "passed": True}

    brain_record, brain_model, brain_two_more = runtime._live_only_brain_inputs(record, model, two_more)

    assert runtime._live_only() is True
    assert brain_record is not record
    assert brain_record["prematch_context"] == {}
    assert brain_record["prematch_goal_profile"] == {}
    assert "xbet_prematch_market" not in brain_record
    assert brain_record["live_momentum"] == record["live_momentum"]
    assert "trained_probability" not in brain_model
    assert "direct" not in brain_model
    assert brain_model["another_goal_live"] == model["another_goal_live"]
    assert brain_two_more == {}

    # PREMATCH is still present on the production record for diagnostics/research.
    assert record["prematch_context"]["home_recent"]
    assert record["prematch_goal_profile"]["active"]["available"] is True
    assert record["xbet_prematch_market"]["available"] is True


def test_live_only_restore_removes_prematch_kickoff_and_lineup_from_decision(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GOOL_LIVE_ONLY", "1")
    experts = {
        "another_goal": {
            "probability": 0.91,
            "diagnostics": {
                "half_prematch_prior": {"probability_after": 0.84},
                "match_intelligence": {
                    "kickoff_prior_used": True,
                    "probability_after": 0.91,
                },
            },
        }
    }
    intelligence = {
        "probability_adjustment": {
            "strategy": "another_goal",
            "before": 0.84,
            "after": 0.91,
            "hazard_pp": 1.2,
            "chance_quality_pp": 0.8,
            "kickoff_total_pp": 1.5,
            "lineup_pp": -0.5,
            "total_pp": 3.0,
        },
        "suitability": {
            "score": 0.99,
            "minimum": 0.58,
            "hard_blocks": [],
            "components": {
                "integrity": 1.0,
                "provider_data": 0.80,
                "history": 0.05,
                "market": 0.90,
                "epoch_evidence": 0.70,
                "kickoff": 0.10,
                "lineup": 0.10,
            },
        },
    }
    record: dict = {}

    out = runtime._restore_live_only_brain(
        record,
        experts,
        intelligence,
        {"another_goal": 0.70},
    )

    # Only LIVE hazard (+1.2pp) and LIVE chance quality (+0.8pp) remain.
    assert experts["another_goal"]["probability"] == pytest.approx(0.72)
    adjustment = out["probability_adjustment"]
    assert adjustment["before"] == pytest.approx(0.70)
    assert adjustment["after"] == pytest.approx(0.72)
    assert adjustment["kickoff_total_pp"] == 0.0
    assert adjustment["lineup_pp"] == 0.0
    assert adjustment["total_pp"] == pytest.approx(2.0)
    assert adjustment["prematch_decision_enabled"] is False

    diagnostics = experts["another_goal"]["diagnostics"]
    assert diagnostics["half_prematch_prior"]["decision_enabled"] is False
    assert diagnostics["match_intelligence"]["kickoff_prior_used"] is False
    assert diagnostics["live_only_brain"]["prematch_probability_contribution_pp"] == 0.0

    # Suitability is rebuilt from the four existing LIVE components only.
    expected = (
        1.0 * (0.22 / 0.74)
        + 0.80 * (0.18 / 0.74)
        + 0.90 * (0.18 / 0.74)
        + 0.70 * (0.16 / 0.74)
    )
    suitability = out["suitability"]
    assert suitability["score"] == pytest.approx(round(expected, 4))
    assert suitability["decision_mode"] == "live_only"
    assert suitability["disabled_decision_components"] == ["history", "kickoff", "lineup"]
    assert out["prematch_decision_enabled"] is False
    assert record["match_intelligence"] is out


def test_hybrid_mode_can_be_explicitly_reenabled(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GOOL_LIVE_ONLY", "0")
    record = {"prematch_context": {"home_recent": [1]}}
    model = {"trained_probability": {"another_goal": 0.8}}
    two_more = {"passed": True}

    brain_record, brain_model, brain_two_more = runtime._live_only_brain_inputs(record, model, two_more)

    assert runtime._live_only() is False
    assert brain_record is record
    assert brain_model is model
    assert brain_two_more is two_more


def test_live_only_another_goal_guard_cannot_read_prematch_profile(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GOOL_LIVE_ONLY", "1")
    record = {"prematch_goal_profile": {"active": {"period": "2H", "current_half_goals": 3}}}
    seen: dict[str, object] = {}

    def fake_guard(decision, guarded_record, experts, market):
        seen["profile"] = guarded_record.get("prematch_goal_profile")
        return "guarded"

    monkeypatch.setattr(runtime, "enforce_another_goal_context", fake_guard)

    result = runtime._enforce_another_goal_context_for_mode("decision", record, {}, None)

    assert result == "guarded"
    assert seen["profile"] == {}
    assert record["prematch_goal_profile"]["active"]["current_half_goals"] == 3
