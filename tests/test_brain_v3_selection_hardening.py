from __future__ import annotations

from gool_bot2 import brain_v3_selection_hardening as selection


def _record(mid: str):
    return {
        "match": {
            "flashscore_event_id": mid,
            "home": f"Home {mid}",
            "away": f"Away {mid}",
            "minute": 67,
        }
    }


def _decision(score: float = 0.80):
    return {
        "active": True,
        "status": "BET",
        "strategy": "another_goal",
        "period": "2H",
        "minute": 67,
        "probability": 0.78,
        "bet_min": 0.72,
        "live_foundation": True,
        "sustained_pressure": True,
        "countercase": {"strong": False},
        "selection_audit": {"score": score},
        "blocks": [],
    }


def _experts():
    return {
        "another_goal": {
            "source": "brain_v3:state_machine",
            "passed": True,
            "state": "PASS",
            "blocks": [],
            "diagnostics": {},
        }
    }


def test_first_observation_waits_for_live_field(monkeypatch):
    selection._reset_pool_for_tests()
    monkeypatch.setattr(selection.time, "monotonic", lambda: 100.0)
    out = selection._apply_tournament(_record("a"), _experts(), _decision())
    assert out["status"] == "READY"
    assert "brain_v3_selection_gathering_field" in out["blocks"]
    assert out["selection_tournament"]["observations"] == 1


def test_mature_strong_candidate_can_bet(monkeypatch):
    selection._reset_pool_for_tests()
    clock = {"now": 100.0}
    monkeypatch.setattr(selection.time, "monotonic", lambda: clock["now"])

    selection._apply_tournament(_record("a"), _experts(), _decision(0.82))
    clock["now"] = 109.0
    out = selection._apply_tournament(_record("a"), _experts(), _decision(0.82))

    assert out["status"] == "BET"
    assert out["selection_tournament"]["verdict"] == "KEEP"
    assert out["selection_tournament"]["observations"] == 2


def test_mature_weaker_candidate_loses_to_stronger_match(monkeypatch):
    selection._reset_pool_for_tests()
    clock = {"now": 100.0}
    monkeypatch.setattr(selection.time, "monotonic", lambda: clock["now"])

    selection._apply_tournament(_record("weak"), _experts(), _decision(0.74))
    selection._apply_tournament(_record("strong"), _experts(), _decision(0.84))

    clock["now"] = 109.0
    out = selection._apply_tournament(_record("weak"), _experts(), _decision(0.74))

    assert out["status"] == "READY"
    assert "brain_v3_selection_stronger_match" in out["blocks"]
    assert out["selection_tournament"]["stronger_match"]["match_id"] == "strong"


def test_first_half_has_stricter_selection_floor(monkeypatch):
    selection._reset_pool_for_tests()
    clock = {"now": 100.0}
    monkeypatch.setattr(selection.time, "monotonic", lambda: clock["now"])

    first = _decision(0.70)
    first.update({"strategy": "goal_before_ht", "period": "1H", "minute": 33, "bet_min": 0.71})
    experts = {"goal_before_ht": {"source": "brain_v3:state_machine", "passed": True, "state": "PASS", "blocks": [], "diagnostics": {}}}
    selection._apply_tournament(_record("fh"), experts, dict(first))

    clock["now"] = 109.0
    out = selection._apply_tournament(_record("fh"), experts, dict(first))
    assert out["status"] == "READY"
    assert "brain_v3_selection_score_too_low" in out["blocks"]
    assert out["selection_tournament"]["floor"] == 0.72
