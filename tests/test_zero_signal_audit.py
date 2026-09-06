from __future__ import annotations

from datetime import datetime, timezone

from gool_bot2 import goal_state_engine as engine
from gool_bot2.goal_state_policy import enforce_goal_state_policy
from gool_bot2.multi_autonomous_steam import build_autonomous_steam_candidates
from gool_bot2.multi_money_flow import evaluate_money_flow
from gool_bot2.multi_router import MarketCandidate, RouterDecision


def test_live_brain_below_70_stays_wait() -> None:
    row = MarketCandidate(
        key="match_total:0.5",
        family="match_total",
        label="ТБ 0.5",
        odd=1.60,
        model_probability=0.66,
        correlation_key="any_next_goal",
        strategy="another_goal",
        source="goal_state_engine:another_goal",
        expert_passed=True,
        market_age_seconds=5.0,
        data_quality=0.30,
        rating=10.0,
        eligible=True,
    )
    decision = RouterDecision(
        status="BET",
        minute=60,
        score=(0, 0),
        winner=row,
        alternatives=[],
        rejected=[],
        reason="legacy",
    )
    experts = {
        "another_goal": {
            "probability": 0.66,
            "metric": "confidence",
            "state": "PASS",
            "passed": True,
        }
    }

    out = enforce_goal_state_policy(decision, experts)

    assert out.status == "WAIT"
    assert out.winner is None
    assert row in out.rejected


def test_raw_live_pressure_pass_can_reach_goal_state_pass(monkeypatch) -> None:
    raw = {
        "confidence_score": 0.581,
        "pressure_score": 0.92,
        "evidence": 4,
        "recent_threat": True,
        "quality_threat": True,
        "prematch": {"matches": 0, "scored_rate": None, "avg_goals_for": None},
        "passed": True,
        "blocks": [],
    }
    monkeypatch.setattr(engine, "side_goal_pressure", lambda record, side: {**raw, "side": side})
    record = {
        "match": {
            "minute": 55,
            "home_score": 0,
            "away_score": 0,
            "is_finished": False,
        },
        "cards": {},
    }

    experts = engine.build_goal_state_experts(record, model_result={}, data_quality=0.50)

    assert experts["home_goal"]["state"] == "PASS"
    assert experts["away_goal"]["state"] == "PASS"
    assert experts["another_goal"]["state"] == "PASS"


def test_recalibrated_steam_requires_a_real_strong_move() -> None:
    record = {
        "match": {
            "flashscore_event_id": "steam-audit",
            "minute": 58,
            "home_score": 0,
            "away_score": 0,
            "is_finished": False,
        }
    }
    market = {
        "captured_at": datetime.now(timezone.utc).isoformat(),
        "score_home": 0,
        "score_away": 0,
        "score_verified": True,
        "score_desync": False,
        "repricing_guard": False,
        "markets": {
            "match_total": [
                {"line": 0.5, "over": 1.65, "under": 2.25},
                {"line": 1.5, "over": 2.25, "under": 1.65},
            ],
            "home_total": [{"line": 0.5, "over": 1.90, "under": 1.90}],
            "away_total": [],
            "first_half_total": [],
            "btts": {"yes": 2.10, "no": 1.70},
        },
        "pressure": {
            "match_total:0.5": {
                "prob_delta_pp": 7.5,
                "one_way_moves": 2,
                "old_odd": 1.88,
            },
            "home_total:0.5": {
                "prob_delta_pp": 2.4,
                "one_way_moves": 1,
                "old_odd": 2.02,
            },
        },
    }

    rows = build_autonomous_steam_candidates(record, market, data_quality=0.60)

    assert any(row.strategy == "steam_another_goal" for row in rows)


def test_non_top_relative_money_can_trigger_without_top_league_turnover() -> None:
    record = {
        "match": {
            "flashscore_event_id": "flow-audit",
            "home": "A",
            "away": "B",
            "league": "Regional League",
            "minute": 60,
            "home_score": 1,
            "away_score": 1,
        },
        "providers": {"flashscore": {"meta": {"goal_timeline": []}}},
        "matchbook_exchange": {
            "available": True,
            "event": {"id": "mb1"},
            "systems": {
                "another_goal": {
                    "available": True,
                    "liquid": True,
                    "period": "FT",
                    "line": 2.5,
                    "market_id": "m1",
                    "market_name": "Total Goals 2.5",
                    "market_status": "open",
                    "volume": 1000.0,
                    "fair_over": 0.61,
                    "over": {
                        "best_back": {"odds": 1.72},
                        "best_lay": {"odds": 1.74},
                    },
                    "flow": {
                        "window_ready_30s": True,
                        "window_ready_60s": False,
                        "volume_delta_30s": 200.0,
                        "fair_over_delta_pp_30s": 1.2,
                    },
                }
            },
        },
    }

    info = evaluate_money_flow(record)

    assert info["eligible"] is True
    assert info["level"] == "NON_TOP_BIG_MONEY"
    assert info["league_tier"] == "non_top"
    assert info["unusual_for_league"] is True
