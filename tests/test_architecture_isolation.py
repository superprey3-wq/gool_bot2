from __future__ import annotations

from gool_bot2.goal_state_policy import enforce_goal_state_policy
from gool_bot2.multi_another_goal_guard import enforce_another_goal_context
from gool_bot2.multi_exchange_confirmation import apply_matchbook_confirmation
from gool_bot2.multi_money_flow import evaluate_money_flow
from gool_bot2.multi_router import MarketCandidate, RouterDecision


def _candidate(*, pressure: float, override: bool, state_probability: float = 0.80) -> MarketCandidate:
    return MarketCandidate(
        key="match_total:0.5",
        family="match_total",
        label="ТБ 0.5",
        odd=1.72,
        model_probability=state_probability,
        correlation_key="any_next_goal",
        strategy="another_goal",
        source="goal_state_engine:another_goal",
        expert_passed=True,
        market_pressure_pp=pressure,
        market_level="STRONG_STEAM" if pressure > 0 else "OPPOSITION",
        market_override=override,
        market_age_seconds=5.0,
        data_quality=0.90,
        rating=10.0,
        eligible=True,
    )


def _decision(row: MarketCandidate) -> RouterDecision:
    return RouterDecision(
        status="BET",
        minute=60,
        score=(0, 0),
        winner=row,
        alternatives=[],
        rejected=[],
        reason="legacy",
    )


def _experts(state: str = "PASS", probability: float = 0.80) -> dict:
    return {
        "another_goal": {
            "probability": probability,
            "metric": "confidence",
            "state": state,
            "passed": state == "PASS",
        }
    }


def test_main_brain_ignores_positive_and_negative_xbet_movement():
    supportive = _candidate(pressure=12.0, override=True)
    opposing = _candidate(pressure=-12.0, override=False)

    a = enforce_goal_state_policy(_decision(supportive), _experts())
    b = enforce_goal_state_policy(_decision(opposing), _experts())

    assert a.status == b.status == "BET"
    assert a.winner is supportive
    assert b.winner is opposing
    assert supportive.rating == opposing.rating
    assert supportive.market_pressure_pp == opposing.market_pressure_pp == 0.0
    assert supportive.market_level == opposing.market_level == "ODDS_ONLY"
    assert supportive.odd == opposing.odd == 1.72


def test_xbet_override_cannot_revive_borderline_main_brain():
    row = _candidate(pressure=15.0, override=True, state_probability=0.69)
    decision = enforce_goal_state_policy(_decision(row), _experts("BORDERLINE", 0.69))

    assert decision.status == "WAIT"
    assert decision.winner is None
    assert "goal_state_borderline" in row.blocks
    assert row.market_override is False


def test_matchbook_and_live_1x2_guards_are_not_main_brain_inputs():
    row = _candidate(pressure=0.0, override=False)
    decision = _decision(row)
    before = row.rating

    assert apply_matchbook_confirmation(decision, {"matchbook_exchange": {"available": True}}) is decision
    assert row.rating == before
    assert enforce_another_goal_context(decision, {}, _experts(), {}) is decision


def _flow_record(*, league: str, volume: float = 15000.0, delta: float = 900.0, fair_pp: float = 1.2) -> dict:
    return {
        "match": {
            "flashscore_event_id": "flow1",
            "home": "Home",
            "away": "Away",
            "league": league,
            "minute": 60,
            "home_score": 1,
            "away_score": 1,
        },
        "providers": {"flashscore": {"meta": {"goal_timeline": []}}},
        "matchbook_exchange": {
            "available": True,
            "event": {"id": "mb-flow"},
            "systems": {
                "another_goal": {
                    "available": True,
                    "liquid": True,
                    "period": "FT",
                    "line": 2.5,
                    "market_id": "m-flow",
                    "market_name": "Total Goals 2.5",
                    "market_status": "open",
                    "volume": volume,
                    "fair_over": 0.61,
                    "over": {
                        "best_back": {"odds": 1.72, "available": 300.0},
                        "best_lay": {"odds": 1.74, "available": 250.0},
                    },
                    "flow": {
                        "window_ready_30s": True,
                        "window_ready_60s": True,
                        "volume_delta_30s": delta,
                        "volume_delta_60s": delta + 100.0,
                        "fair_over_delta_pp_30s": fair_pp,
                        "fair_over_delta_pp_60s": fair_pp + 0.1,
                    },
                }
            },
        },
    }


def test_non_top_league_large_absolute_money_is_its_own_signal(monkeypatch):
    monkeypatch.setenv("MATCHBOOK_FLOW_NON_TOP_BIG_MARKET_VOLUME", "2500")
    monkeypatch.setenv("MATCHBOOK_FLOW_NON_TOP_BIG_DELTA_30S", "750")
    monkeypatch.setenv("MATCHBOOK_FLOW_NON_TOP_MIN_RELATIVE_PCT", "3")
    monkeypatch.setenv("MATCHBOOK_FLOW_NON_TOP_MIN_FAIR_PP", "1")

    info = evaluate_money_flow(_flow_record(league="Regional League"))

    assert info["eligible"] is True
    assert info["level"] == "NON_TOP_BIG_MONEY"
    assert info["league_tier"] == "non_top"
    assert info["unusual_for_league"] is True
    assert info["volume_signal"] == "non_top_big_money"
    assert info["market_volume"] == 15000.0
    assert info["volume_delta"] == 900.0


def test_same_big_money_does_not_get_non_top_shortcut_in_premier_league(monkeypatch):
    monkeypatch.setenv("MATCHBOOK_FLOW_NON_TOP_BIG_MARKET_VOLUME", "2500")
    monkeypatch.setenv("MATCHBOOK_FLOW_NON_TOP_BIG_DELTA_30S", "750")
    monkeypatch.setenv("MATCHBOOK_FLOW_NON_TOP_MIN_RELATIVE_PCT", "3")
    monkeypatch.setenv("MATCHBOOK_FLOW_NON_TOP_MIN_FAIR_PP", "1")

    info = evaluate_money_flow(_flow_record(league="Premier League"))

    assert info["eligible"] is False
    assert info["reason"] == "money_flow_threshold_not_reached"
    assert info["league_tier"] == "top"
    assert info["volume_signal"] == "none"
