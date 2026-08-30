from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class GoalEvent:
    minute: float
    period: int


@dataclass(frozen=True)
class Targets:
    another_goal: int
    goal_before_ht: int | None
    two_plus_goals_second_half: int | None


def build_targets(snapshot_minute: float, snapshot_period: int, goals: list[GoalEvent]) -> Targets:
    """Build leakage-safe labels for the three first prediction heads."""
    future_goals = [g for g in goals if g.minute > snapshot_minute]
    another_goal = int(bool(future_goals))

    goal_before_ht = None
    if snapshot_period == 1:
        goal_before_ht = int(any(g.period == 1 and g.minute > snapshot_minute for g in goals))

    # This head predicts the complete second-half goal count, therefore it is
    # trainable only before the second half has started. Live 2H snapshots would
    # otherwise mix already-observed goals into the target definition.
    two_plus_goals_second_half = None
    if snapshot_period == 1:
        second_half_goals = sum(1 for g in goals if g.period == 2)
        two_plus_goals_second_half = int(second_half_goals >= 2)

    return Targets(
        another_goal=another_goal,
        goal_before_ht=goal_before_ht,
        two_plus_goals_second_half=two_plus_goals_second_half,
    )
