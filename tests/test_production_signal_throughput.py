from __future__ import annotations

from gool_bot2 import brain_primary_mode as primary
from gool_bot2.brain_v3_selection_hardening import _reset_pool_for_tests
from gool_bot2.production_signal_throughput import (
    _brain_v3_dynamic_analyzer,
    _full_match_routing_experts,
    _full_match_strategy,
    _production_brain_audit,
)
from gool_bot2.storage_live_collector import StorageLiveSnapshotCollector


def test_full_match_collector_keeps_real_detail_through_both_halves():
    assert StorageLiveSnapshotCollector._entry_window(1)
    assert StorageLiveSnapshotCollector._entry_window(40)
    assert StorageLiveSnapshotCollector._entry_window(45)
    assert StorageLiveSnapshotCollector._entry_window(46)
    assert StorageLiveSnapshotCollector._entry_window(85)
    assert StorageLiveSnapshotCollector._entry_window(95)
    assert not StorageLiveSnapshotCollector._entry_window(0)
    assert not StorageLiveSnapshotCollector._entry_window(96)
    assert StorageLiveSnapshotCollector._eligible_for_detail(85, False)
    assert not StorageLiveSnapshotCollector._eligible_for_detail(45, True)


def test_full_match_routing_has_no_old_35_75_dead_zones():
    experts = {
        "goal_before_ht": {"probability": 0.75},
        "another_goal": {"probability": 0.75},
    }
    assert _full_match_strategy({"minute": 40}) == "goal_before_ht"
    assert list(_full_match_routing_experts({"minute": 40}, experts)) == ["goal_before_ht"]
    assert _full_match_strategy({"minute": 85}) == "another_goal"
    assert list(_full_match_routing_experts({"minute": 85}, experts)) == ["another_goal"]
    assert _full_match_strategy({"minute": 45, "is_halftime": True}) is None
    assert _full_match_strategy({"minute": 96}) is None


def test_strong_live_pass_is_not_killed_by_second_fixed_70_gate(monkeypatch):
    monkeypatch.setattr(primary, "_active_strategy", _full_match_strategy)
    wrapped = _brain_v3_dynamic_analyzer(primary.analyze_brain_primary_match)
    match = {
        "flashscore_event_id": "strong-live",
        "minute": 40,
        "home_score": 0,
        "away_score": 0,
        "is_halftime": False,
        "is_finished": False,
    }
    experts = {
        "goal_before_ht": {
            "source": "brain_v3:state_machine",
            "probability": 0.65,
            "passed": True,
            "state": "PASS",
            "blocks": [],
            "diagnostics": {
                "brain_v3": {
                    "status": "BET",
                    "entry_mode": "STRONG_LIVE",
                    "bet_min": 0.64,
                }
            },
        }
    }
    decision = wrapped(match, None, experts, data_quality=0.80)
    assert decision.status == "BET"
    assert decision.winner is not None
    assert decision.winner.rating == 65.0
    assert "brain_v3_dynamic_floor" in decision.winner.reason_tags


def test_plain_65_percent_pass_does_not_bypass_brain_v3_safety(monkeypatch):
    monkeypatch.setattr(primary, "_active_strategy", _full_match_strategy)
    wrapped = _brain_v3_dynamic_analyzer(primary.analyze_brain_primary_match)
    match = {
        "flashscore_event_id": "plain",
        "minute": 40,
        "home_score": 0,
        "away_score": 0,
        "is_halftime": False,
        "is_finished": False,
    }
    experts = {
        "goal_before_ht": {
            "source": "legacy:test",
            "probability": 0.65,
            "passed": True,
            "state": "PASS",
            "blocks": [],
        }
    }
    decision = wrapped(match, None, experts, data_quality=0.80)
    assert decision.status == "WAIT"


def _decision() -> dict:
    return {
        "version": 3,
        "active": True,
        "status": "BET",
        "strategy": "another_goal",
        "period": "2H",
        "minute": 60,
        "score": [1, 1],
        "probability": 0.82,
        "live_probability": 0.79,
        "confidence_score": 82.0,
        "confidence_cap": 0.92,
        "bet_min": 0.70,
        "ready_min": 0.60,
        "minutes_left": 35.0,
        "expected_goals_remaining": 1.45,
        "data_quality": 0.88,
        "match_state": "HOME_SIEGE",
        "pressure_index": 0.88,
        "home_pressure": 0.88,
        "away_pressure": 0.30,
        "home_trend": "RISING",
        "away_trend": "STEADY",
        "recent": {
            "xg5": 0.38,
            "xg10": 0.65,
            "shots5": 6.0,
            "sot5": 3.0,
            "big5": 1.0,
        },
        "live_foundation": True,
        "sustained_pressure": True,
        "thoughts": [],
        "blocks": [],
    }


def _record(scan: int, confirmed: int) -> dict:
    return {
        "runtime_field_scan_id": scan,
        "runtime_field_scan_confirmed_round": confirmed,
        "runtime_market_recheck": False,
        "match": {
            "flashscore_event_id": "two-football-scans",
            "home": "Home",
            "away": "Away",
            "minute": 60,
            "home_score": 1,
            "away_score": 1,
        },
    }


def _experts() -> dict:
    return {
        "another_goal": {
            "source": "brain_v3:state_machine",
            "probability": 0.82,
            "passed": True,
            "state": "PASS",
            "blocks": [],
            "diagnostics": {},
        }
    }


def test_two_football_scans_complete_selection_without_xbet_recheck(monkeypatch):
    monkeypatch.setenv("GOOL_BRAIN_V3_SELECTION_MATURITY_SECONDS", "0")
    _reset_pool_for_tests()

    first = _production_brain_audit(_record(1, 0), _experts(), _decision())
    assert first["status"] == "READY"
    assert "brain_v3_selection_gathering_field" in first["blocks"]

    second_experts = _experts()
    second = _production_brain_audit(_record(2, 1), second_experts, _decision())
    assert second["status"] == "BET"
    assert "brain_v3_selection_field_not_complete" not in second["blocks"]
    assert second["selection_tournament"]["fresh_field_scans"] >= 2
    assert second_experts["another_goal"]["passed"] is True
