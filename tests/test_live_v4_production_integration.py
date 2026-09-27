from pathlib import Path

import gool_bot2.multi_runtime as runtime
from gool_bot2.live_goal_brain_v4 import LiveGoalDecision


def _decision(decision: str, probability: float = .75, confidence: float = .80):
    return LiveGoalDecision("ANOTHER_GOAL", decision, probability, confidence, probability * confidence, ("test",), "provider_xg")


def test_live_v4_shadow_does_not_mutate_experts(monkeypatch, tmp_path: Path):
    monkeypatch.setenv("GOOL_LIVE_V4_MODE", "shadow")
    monkeypatch.setattr(runtime, "evaluate_live_goals", lambda record: [_decision("BET")])
    experts = {"another_goal": {"probability": .61, "passed": False, "source": "old"}}
    runtime._apply_live_v4({"match": {"flashscore_event_id": "x", "minute": 60}}, experts, tmp_path / "a.jsonl")
    assert experts["another_goal"]["probability"] == .61
    assert experts["another_goal"]["passed"] is False


def test_live_v4_active_policy_promotes_football_gate(monkeypatch, tmp_path: Path):
    monkeypatch.setenv("GOOL_LIVE_V4_MODE", "active")
    monkeypatch.setattr(runtime, "evaluate_live_goals", lambda record: [_decision("BET", .77, .81)])
    experts = {"another_goal": {"probability": .61, "passed": False, "source": "old", "blocks": ["old"]}}
    runtime._apply_live_v4({"match": {"flashscore_event_id": "x", "minute": 60}}, experts, tmp_path / "a.jsonl")
    row = experts["another_goal"]
    assert row["probability"] == .77
    assert row["confidence"] == .81
    assert row["passed"] is True
    assert row["source"] == "live_v4_policy"
    assert row["brain_shadow_decision"] == "BET"


def test_live_v4_active_policy_can_override_old_brain_veto(monkeypatch, tmp_path: Path):
    monkeypatch.setenv("GOOL_LIVE_V4_MODE", "active")
    monkeypatch.setattr(runtime, "evaluate_live_goals", lambda record: [_decision("NO_BET", .75, .80)])
    experts = {"another_goal": {"probability": .70, "passed": True, "source": "old"}}
    runtime._apply_live_v4({"match": {"flashscore_event_id": "x", "minute": 60}}, experts, tmp_path / "a.jsonl")
    row = experts["another_goal"]
    assert row["passed"] is False
    assert "live_v4_policy_rank_too_low" in row["blocks"]
    assert row["brain_shadow_decision"] == "NO_BET"
