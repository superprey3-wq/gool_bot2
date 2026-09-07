from __future__ import annotations

from gool_bot2 import brain_v3_selection_hardening as selection


def _record(mid: str, *, scan: int = 1, confirmed: int = 0, market_recheck: bool = False):
    row = {
        "match": {
            "flashscore_event_id": mid,
            "home": f"Home {mid}",
            "away": f"Away {mid}",
            "minute": 67,
            "home_score": 1,
            "away_score": 1,
        },
        "runtime_field_scan_id": scan,
        "runtime_field_scan_confirmed_round": confirmed,
    }
    if market_recheck:
        row["runtime_market_recheck"] = True
    return row


def _decision(score: float = 0.80, **updates):
    row = {
        "active": True,
        "status": "BET",
        "strategy": "another_goal",
        "period": "2H",
        "minute": 67,
        "minutes_left": 28,
        "probability": 0.78,
        "live_probability": 0.76,
        "expected_goals_remaining": 1.43,
        "data_quality": 0.82,
        "bet_min": 0.72,
        "live_foundation": True,
        "sustained_pressure": True,
        "countercase": {"strong": False, "no_more_goal_risk": 0.22},
        "selection_audit": {"score": score},
        "blocks": [],
    }
    row.update(updates)
    return row


def _experts(strategy: str = "another_goal"):
    return {
        strategy: {
            "source": "brain_v3:state_machine",
            "passed": True,
            "state": "PASS",
            "blocks": [],
            "diagnostics": {},
        }
    }


def test_first_field_scan_never_bets(monkeypatch):
    selection._reset_pool_for_tests()
    monkeypatch.setattr(selection.time, "monotonic", lambda: 100.0)
    out = selection._apply_tournament(_record("a", scan=1, confirmed=0), _experts(), _decision())
    assert out["status"] == "READY"
    assert "brain_v3_selection_gathering_field" in out["blocks"]
    assert out["selection_tournament"]["fresh_field_scans"] == 1


def test_market_recheck_does_not_fake_second_football_scan(monkeypatch):
    selection._reset_pool_for_tests()
    clock = {"now": 100.0}
    monkeypatch.setattr(selection.time, "monotonic", lambda: clock["now"])

    selection._apply_tournament(_record("a", scan=1, confirmed=0), _experts(), _decision())
    clock["now"] = 110.0
    out = selection._apply_tournament(
        _record("a", scan=1, confirmed=1, market_recheck=True),
        _experts(),
        _decision(),
    )
    assert out["status"] == "READY"
    assert out["selection_tournament"]["fresh_field_scans"] == 1


def test_two_scans_must_finish_before_final_bet(monkeypatch):
    selection._reset_pool_for_tests()
    clock = {"now": 100.0}
    monkeypatch.setattr(selection.time, "monotonic", lambda: clock["now"])

    selection._apply_tournament(_record("a", scan=1, confirmed=0), _experts(), _decision(0.82))
    clock["now"] = 140.0
    second = selection._apply_tournament(_record("a", scan=2, confirmed=1), _experts(), _decision(0.82))
    assert second["status"] == "READY"
    assert "brain_v3_selection_field_not_complete" in second["blocks"]

    clock["now"] = 145.0
    final = selection._apply_tournament(
        _record("a", scan=2, confirmed=2, market_recheck=True),
        _experts(),
        _decision(0.82),
    )
    assert final["status"] == "BET"
    assert final["selection_tournament"]["verdict"] == "KEEP"
    assert final["selection_tournament"]["fresh_field_scans"] == 2
    assert final["selection_tournament"]["field_complete"] is True


def test_weaker_candidate_loses_to_stronger_field_match(monkeypatch):
    selection._reset_pool_for_tests()
    clock = {"now": 100.0}
    monkeypatch.setattr(selection.time, "monotonic", lambda: clock["now"])

    selection._apply_tournament(_record("strong", scan=1, confirmed=0), _experts(), _decision(0.84))
    selection._apply_tournament(_record("weak", scan=1, confirmed=0), _experts(), _decision(0.74))
    clock["now"] = 140.0
    selection._apply_tournament(_record("strong", scan=2, confirmed=1), _experts(), _decision(0.84))
    selection._apply_tournament(_record("weak", scan=2, confirmed=1), _experts(), _decision(0.74))

    clock["now"] = 145.0
    out = selection._apply_tournament(
        _record("weak", scan=2, confirmed=2, market_recheck=True),
        _experts(),
        _decision(0.74),
    )
    assert out["status"] == "READY"
    assert "brain_v3_selection_stronger_match" in out["blocks"]
    assert out["selection_tournament"]["stronger_match"]["match_id"] == "strong"


def test_candidate_that_deteriorates_between_scans_is_blocked(monkeypatch):
    selection._reset_pool_for_tests()
    clock = {"now": 100.0}
    monkeypatch.setattr(selection.time, "monotonic", lambda: clock["now"])

    selection._apply_tournament(_record("drop", scan=1, confirmed=0), _experts(), _decision(0.84, probability=0.82))
    clock["now"] = 140.0
    out = selection._apply_tournament(
        _record("drop", scan=2, confirmed=1),
        _experts(),
        _decision(0.76, probability=0.75),
    )
    assert out["status"] == "READY"
    assert "brain_v3_selection_candidate_deteriorating" not in out["blocks"]  # field barrier wins first
    assert out["selection_tournament"]["deteriorating"] is True

    clock["now"] = 145.0
    out = selection._apply_tournament(
        _record("drop", scan=2, confirmed=2, market_recheck=True),
        _experts(),
        _decision(0.76, probability=0.75),
    )
    assert out["status"] == "READY"
    assert "brain_v3_selection_candidate_deteriorating" in out["blocks"]


def test_self_check_blocks_probability_rescued_by_context(monkeypatch):
    selection._reset_pool_for_tests()
    clock = {"now": 100.0}
    monkeypatch.setattr(selection.time, "monotonic", lambda: clock["now"])

    thin = _decision(
        0.80,
        probability=0.73,
        live_probability=0.60,
        expected_goals_remaining=0.92,
        minute=71,
        minutes_left=24,
    )
    out = selection._apply_tournament(_record("thin", scan=1, confirmed=0), _experts(), thin)
    assert out["status"] == "READY"
    assert "brain_v3_selection_time_insufficient" in out["blocks"]
    assert out["self_check"]["passed"] is False


def test_first_half_has_stricter_selection_floor(monkeypatch):
    selection._reset_pool_for_tests()
    clock = {"now": 100.0}
    monkeypatch.setattr(selection.time, "monotonic", lambda: clock["now"])

    first = _decision(
        0.70,
        strategy="goal_before_ht",
        period="1H",
        minute=31,
        minutes_left=16,
        bet_min=0.71,
        live_probability=0.72,
        expected_goals_remaining=1.28,
    )
    experts = _experts("goal_before_ht")
    selection._apply_tournament(_record("fh", scan=1, confirmed=0), experts, dict(first))
    clock["now"] = 140.0
    selection._apply_tournament(_record("fh", scan=2, confirmed=1), experts, dict(first))
    clock["now"] = 145.0
    out = selection._apply_tournament(
        _record("fh", scan=2, confirmed=2, market_recheck=True),
        experts,
        dict(first),
    )
    assert out["status"] == "READY"
    assert "brain_v3_selection_score_too_low" in out["blocks"]
    assert out["selection_tournament"]["floor"] == 0.72
