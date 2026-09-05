from gool_bot2.multi_concept import (
    FIRST_HALF_STRATEGY,
    MIN_BET_ODD,
    SECOND_HALF_STRATEGY,
    enforce_entry_cutoff,
    ordinary_strategy,
    routing_experts,
)
from gool_bot2.multi_router import MarketCandidate, RouterDecision


def _experts() -> dict:
    return {
        "goal_before_ht": {"probability": 0.80, "passed": True},
        "another_goal": {"probability": 0.79, "passed": True},
        "two_more_goals": {"probability": 0.78, "passed": True},
        "home_goal": {"probability": 0.77, "passed": True},
        "away_goal": {"probability": 0.76, "passed": True},
        "btts": {"probability": 0.75, "passed": True},
    }


def _decision(minute: int, *, source: str = "gool") -> RouterDecision:
    winner = MarketCandidate(
        key="match_total:0.5",
        family="match_total",
        strategy="steam_another_goal" if source.startswith("1xbet:") else "another_goal",
        label="ТБ 0.5",
        odd=1.55,
        model_probability=0.80,
        source=source,
        rating=80.0,
    )
    return RouterDecision(
        status="BET",
        minute=minute,
        score=(0, 0),
        winner=winner,
        alternatives=[],
        rejected=[],
        reason="test",
    )


def test_first_half_routes_only_goal_before_ht_through_35() -> None:
    for minute in (1, 15, 30, 35):
        match = {"minute": minute, "is_halftime": False, "is_finished": False}
        assert ordinary_strategy(match) == FIRST_HALF_STRATEGY
        assert set(routing_experts(match, _experts())) == {"goal_before_ht"}


def test_first_half_has_no_new_ordinary_entry_after_35() -> None:
    for minute in (36, 40, 45):
        match = {"minute": minute, "is_halftime": False, "is_finished": False}
        assert ordinary_strategy(match) is None
        assert routing_experts(match, _experts()) == {}


def test_halftime_routes_no_ordinary_system() -> None:
    match = {"minute": 45, "is_halftime": True, "is_finished": False}
    assert ordinary_strategy(match) is None
    assert routing_experts(match, _experts()) == {}


def test_second_half_routes_only_another_goal_from_46_through_75() -> None:
    for minute in (46, 60, 75):
        match = {"minute": minute, "is_halftime": False, "is_finished": False}
        assert ordinary_strategy(match) == SECOND_HALF_STRATEGY
        assert set(routing_experts(match, _experts())) == {"another_goal"}


def test_after_75_no_ordinary_gool_entry() -> None:
    match = {"minute": 76, "is_halftime": False, "is_finished": False}
    assert ordinary_strategy(match) is None
    assert routing_experts(match, _experts()) == {}


def test_global_cutoff_allows_entry_at_75() -> None:
    decision = enforce_entry_cutoff(_decision(75))
    assert decision.status == "BET"
    assert decision.winner is not None


def test_global_cutoff_blocks_ordinary_entry_after_75() -> None:
    decision = enforce_entry_cutoff(_decision(76))
    assert decision.status == "WAIT"
    assert decision.winner is None
    assert decision.rejected
    assert "concept_entry_after_75" in decision.rejected[0].blocks


def test_global_cutoff_also_blocks_autonomous_steam_after_75() -> None:
    decision = enforce_entry_cutoff(_decision(76, source="1xbet:autonomous_steam"))
    assert decision.status == "WAIT"
    assert decision.winner is None
    assert decision.rejected
    assert decision.rejected[0].source == "1xbet:autonomous_steam"


def test_finished_match_routes_no_ordinary_system() -> None:
    match = {"minute": 70, "is_halftime": False, "is_finished": True}
    assert ordinary_strategy(match) is None
    assert routing_experts(match, _experts()) == {}


def test_concept_keeps_absolute_minimum_odd_at_1_50() -> None:
    assert MIN_BET_ODD == 1.50
