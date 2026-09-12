from __future__ import annotations

from gool_bot2.brain_v3_strong_live import apply_strong_live_entry
from gool_bot2 import brain_v3_selection_hardening as selection


def _experts(strategy: str = "goal_before_ht") -> dict:
    return {
        strategy: {
            "source": "brain_v3:state_machine",
            "probability": 0.6495,
            "passed": False,
            "state": "BORDERLINE",
            "blocks": ["brain_v3_probability_below_bet"],
            "diagnostics": {},
        }
    }


def _decision(**updates) -> dict:
    row = {
        "active": True,
        "status": "READY",
        "strategy": "goal_before_ht",
        "period": "1H",
        "minute": 21,
        "score": [0, 0],
        "minutes_left": 26,
        "match_state": "AWAY_BUILDING",
        "home_pressure": 0.15,
        "away_pressure": 0.42,
        "pressure_index": 0.4352,
        "home_trend": "STEADY",
        "away_trend": "RISING",
        "probability": 0.6495,
        "live_probability": 0.6269,
        "expected_goals_remaining": 0.9859,
        "data_quality": 0.59,
        "bet_min": 0.70,
        "ready_min": 0.60,
        "recent": {
            "xg5": 0.23,
            "xg10": 0.26,
            "shots5": 2.0,
            "sot5": 2.0,
            "big5": 0.0,
        },
        "live_foundation": True,
        "sustained_pressure": True,
        "late_draw": False,
        "blocks": ["brain_v3_probability_below_bet"],
        "thoughts": [],
    }
    row.update(updates)
    return row


def _record(scan: int, confirmed: int, *, market_recheck: bool = False) -> dict:
    row = {
        "match": {
            "flashscore_event_id": "strong-live-test",
            "home": "Home",
            "away": "Away",
            "minute": 21,
            "home_score": 0,
            "away_score": 0,
        },
        "runtime_field_scan_id": scan,
        "runtime_field_scan_confirmed_round": confirmed,
    }
    if market_recheck:
        row["runtime_market_recheck"] = True
    return row


def test_strong_live_can_promote_confirmed_football_below_standard_70() -> None:
    record = _record(1, 0)
    experts = _experts()

    out = apply_strong_live_entry(record, experts, _decision())

    assert out["status"] == "BET"
    assert out["entry_mode"] == "STRONG_LIVE"
    assert out["base_bet_min"] == 0.70
    assert out["bet_min"] == 0.64
    assert out["strong_live"]["qualified"] is True
    assert out["strong_live"]["evidence_count"] == 2
    assert out["strong_live"]["bookmaker_required"] is False
    assert "brain_v3_probability_below_bet" not in out["blocks"]
    assert experts["goal_before_ht"]["passed"] is True


def test_strong_live_never_rescues_a_calm_match() -> None:
    record = _record(1, 0)
    experts = _experts()

    out = apply_strong_live_entry(
        record,
        experts,
        _decision(match_state="CALM", pressure_index=0.20),
    )

    assert out["status"] == "READY"
    assert out["entry_mode"] == "STANDARD"
    assert out["bet_min"] == 0.70
    assert out["strong_live"]["qualified"] is False
    assert experts["goal_before_ht"]["passed"] is False


def test_strong_live_never_relaxes_a_three_goal_lead() -> None:
    out = apply_strong_live_entry(
        _record(1, 0),
        _experts(),
        _decision(score=[3, 0]),
    )

    assert out["status"] == "READY"
    assert out["strong_live"]["qualified"] is False


def test_strong_live_still_requires_two_completed_field_scans(monkeypatch) -> None:
    selection._reset_pool_for_tests()
    clock = {"now": 100.0}
    monkeypatch.setattr(selection.time, "monotonic", lambda: clock["now"])

    def fresh(scan: int, confirmed: int, *, market_recheck: bool = False):
        record = _record(scan, confirmed, market_recheck=market_recheck)
        experts = _experts()
        decision = apply_strong_live_entry(record, experts, _decision())
        decision["countercase"] = {"strong": False, "no_more_goal_risk": 0.40}
        decision["selection_audit"] = {"score": 0.55}
        return record, experts, decision

    record, experts, decision = fresh(1, 0)
    first = selection._apply_tournament(record, experts, decision)
    assert first["status"] == "READY"
    assert "brain_v3_selection_gathering_field" in first["blocks"]

    clock["now"] = 140.0
    record, experts, decision = fresh(2, 1)
    second = selection._apply_tournament(record, experts, decision)
    assert second["status"] == "READY"
    assert "brain_v3_selection_field_not_complete" in second["blocks"]

    clock["now"] = 145.0
    record, experts, decision = fresh(2, 2, market_recheck=True)
    final = selection._apply_tournament(record, experts, decision)
    assert final["status"] == "BET"
    assert final["selection_tournament"]["entry_mode"] == "STRONG_LIVE"
    assert final["selection_tournament"]["floor"] == 0.54
    assert final["selection_tournament"]["field_complete"] is True
