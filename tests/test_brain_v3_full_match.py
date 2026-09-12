from __future__ import annotations

from gool_bot2.brain_v3_full_match import full_match_strategy


def test_brain_v3_analyzes_entire_first_half() -> None:
    for minute in (1, 20, 35, 36, 40, 45):
        strategy, period, horizon = full_match_strategy(minute, False)
        assert strategy == "goal_before_ht"
        assert period == "1H"
        assert horizon == 47


def test_brain_v3_analyzes_entire_second_half_and_stoppage() -> None:
    for minute in (46, 60, 73, 74, 75, 80, 90, 95):
        strategy, period, horizon = full_match_strategy(minute, False)
        assert strategy == "another_goal"
        assert period == "2H"
        assert horizon == 95


def test_brain_v3_pauses_only_for_halftime_or_outside_match() -> None:
    assert full_match_strategy(45, True) == (None, None, 0)
    assert full_match_strategy(0, False) == (None, None, 0)
    assert full_match_strategy(96, False) == (None, None, 0)
