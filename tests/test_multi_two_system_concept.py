from gool_bot2.multi_concept import (
    FIRST_HALF_STRATEGY,
    MIN_BET_ODD,
    SECOND_HALF_STRATEGY,
    ordinary_strategy,
    routing_experts,
)


def _experts() -> dict:
    return {
        "goal_before_ht": {"probability": 0.80, "passed": True},
        "another_goal": {"probability": 0.79, "passed": True},
        "two_more_goals": {"probability": 0.78, "passed": True},
        "home_goal": {"probability": 0.77, "passed": True},
        "away_goal": {"probability": 0.76, "passed": True},
        "btts": {"probability": 0.75, "passed": True},
    }


def test_first_half_routes_only_goal_before_ht() -> None:
    match = {"minute": 30, "is_halftime": False, "is_finished": False}
    assert ordinary_strategy(match) == FIRST_HALF_STRATEGY
    assert set(routing_experts(match, _experts())) == {"goal_before_ht"}


def test_halftime_routes_no_ordinary_system() -> None:
    match = {"minute": 45, "is_halftime": True, "is_finished": False}
    assert ordinary_strategy(match) is None
    assert routing_experts(match, _experts()) == {}


def test_second_half_routes_only_another_goal_through_75() -> None:
    for minute in (46, 60, 75):
        match = {"minute": minute, "is_halftime": False, "is_finished": False}
        assert ordinary_strategy(match) == SECOND_HALF_STRATEGY
        assert set(routing_experts(match, _experts())) == {"another_goal"}


def test_after_75_no_ordinary_gool_entry() -> None:
    match = {"minute": 76, "is_halftime": False, "is_finished": False}
    assert ordinary_strategy(match) is None
    assert routing_experts(match, _experts()) == {}


def test_finished_match_routes_no_ordinary_system() -> None:
    match = {"minute": 70, "is_halftime": False, "is_finished": True}
    assert ordinary_strategy(match) is None
    assert routing_experts(match, _experts()) == {}


def test_concept_keeps_absolute_minimum_odd_at_1_50() -> None:
    assert MIN_BET_ODD == 1.50
