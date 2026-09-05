from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import gool_bot2.goal_state_engine as goal_state_engine
import gool_bot2.xbet_market_worker as xbet_worker
from gool_bot2.multi_concept import MIN_BET_ODD, ordinary_strategy
from gool_bot2.xbet_market_demand import load_active_demands, request_live_market


def _record(minute: int = 20) -> dict:
    return {
        "match": {
            "flashscore_event_id": "fs-1",
            "home": "Arsenal",
            "away": "Chelsea",
            "league": "Premier League",
            "minute": minute,
            "home_score": 0,
            "away_score": 0,
            "is_finished": False,
            "is_halftime": False,
        }
    }


def test_ordinary_gool_requests_xbet_only_after_football_candidate(tmp_path, monkeypatch):
    demand = tmp_path / "demand.json"
    monkeypatch.setenv("XBET_MARKET_DEMAND_PATH", str(demand))

    hard_no = {"goal_before_ht": {"state": "HARD_NO", "probability": 0.80, "passed": False}}
    assert request_live_market(_record(20), hard_no) is None
    assert load_active_demands(demand) == {}

    borderline = {"goal_before_ht": {"state": "BORDERLINE", "probability": 0.68, "passed": False}}
    row = request_live_market(_record(20), borderline)
    assert row is not None
    assert row["strategy"] == "goal_before_ht"
    assert row["football_state"] == "BORDERLINE"
    assert set(load_active_demands(demand)) == {"fs-1"}


def test_second_half_demand_starts_at_46_and_first_half_runs_through_35(tmp_path, monkeypatch):
    monkeypatch.setenv("XBET_MARKET_DEMAND_PATH", str(tmp_path / "demand.json"))
    experts = {
        "goal_before_ht": {"state": "PASS", "probability": 0.78, "passed": True},
        "another_goal": {"state": "PASS", "probability": 0.77, "passed": True},
    }

    assert ordinary_strategy(_record(35)["match"]) == "goal_before_ht"
    assert request_live_market(_record(35), experts)["strategy"] == "goal_before_ht"
    assert ordinary_strategy(_record(36)["match"]) is None
    assert request_live_market(_record(36), experts) is None
    assert ordinary_strategy(_record(46)["match"]) == "another_goal"
    assert request_live_market(_record(46), experts)["strategy"] == "another_goal"
    assert ordinary_strategy(_record(75)["match"]) == "another_goal"
    assert ordinary_strategy(_record(76)["match"]) is None
    assert MIN_BET_ODD == 1.50


def test_demand_collector_watches_every_live_match_and_prioritises_brain_demand(tmp_path, monkeypatch):
    collector = xbet_worker.DemandDrivenXBetMarketCollector(
        Path(tmp_path / "state.json"), Path(tmp_path / "history.jsonl")
    )
    monkeypatch.setattr(xbet_worker, "load_active_demands", lambda: {"demand": {"strategy": "another_goal"}})

    matches = [
        SimpleNamespace(provider_match_id="demand", minute=89, league="Regional League", is_finished=False),
        SimpleNamespace(provider_match_id="m1", minute=3, league="League A", is_finished=False),
        SimpleNamespace(provider_match_id="m2", minute=36, league="League B", is_finished=False),
        SimpleNamespace(provider_match_id="m3", minute=76, league="League C", is_finished=False),
        SimpleNamespace(provider_match_id="m4", minute=95, league="League D", is_finished=False),
        SimpleNamespace(provider_match_id="done", minute=90, league="League E", is_finished=True),
    ]
    selected, stats = collector._select_matches(matches)
    ids = [str(row.provider_match_id) for row in selected]
    assert ids == ["demand", "m1", "m2", "m3", "m4"]
    assert stats["demanded"] == 1
    assert stats["live_watch"] == 5
    assert stats["background"] == 4


def test_first_half_can_pass_before_10_only_with_real_live_evidence(monkeypatch):
    def strong_pressure(record, side):
        return {
            "confidence_score": 0.82 if side == "home" else 0.76,
            "pressure_score": 0.90 if side == "home" else 0.82,
            "evidence": 5,
            "recent_threat": True,
            "quality_threat": True,
            "prematch": {},
            "passed": True,
            "blocks": [],
        }

    monkeypatch.setattr(goal_state_engine, "side_goal_pressure", strong_pressure)
    record = _record(5)
    experts = goal_state_engine.build_goal_state_experts(record, model_result={})
    assert experts["goal_before_ht"]["state"] == "PASS"
    assert experts["goal_before_ht"]["probability"] >= 0.70


def test_first_half_still_no_data_early_without_live_evidence(monkeypatch):
    def empty_pressure(record, side):
        return {
            "confidence_score": None,
            "pressure_score": None,
            "evidence": 0,
            "recent_threat": False,
            "quality_threat": False,
            "prematch": {},
            "passed": False,
            "blocks": [],
        }

    monkeypatch.setattr(goal_state_engine, "side_goal_pressure", empty_pressure)
    experts = goal_state_engine.build_goal_state_experts(_record(5), model_result={})
    assert experts["goal_before_ht"]["state"] == "NO_DATA"
