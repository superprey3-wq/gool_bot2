from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Iterable, Literal


Side = Literal["home", "away"]


@dataclass(frozen=True)
class ArchiveGoalEvent:
    """Goal known from the historical event timeline.

    `minute` is the normalized elapsed match minute used for ordering. The raw
    provider clock should still be preserved by the archive ingestor.
    """

    minute: float
    period: int
    side: Side


@dataclass(frozen=True)
class ArchiveMatch:
    match_id: str
    kickoff_at: datetime
    goals: tuple[ArchiveGoalEvent, ...]


@dataclass(frozen=True)
class ArchiveTrainingExample:
    """One leakage-safe learning row reconstructed at historical cutoff t."""

    match_id: str
    kickoff_at: datetime
    minute: float
    period: int
    home_score: int
    away_score: int
    another_goal: int
    goal_before_ht: int | None
    two_plus_goals_second_half: int | None


def period_for_minute(minute: float) -> int:
    return 1 if minute <= 45.0 else 2


def score_as_of(goals: Iterable[ArchiveGoalEvent], minute: float) -> tuple[int, int]:
    """Return score using only goals observed at or before cutoff minute."""
    home = 0
    away = 0
    for goal in goals:
        if goal.minute > minute:
            continue
        if goal.side == "home":
            home += 1
        else:
            away += 1
    return home, away


def build_archive_example(match: ArchiveMatch, minute: float) -> ArchiveTrainingExample:
    """Reconstruct a historical snapshot and the three GOOL targets.

    Features in this base row are deliberately limited to information that can
    be reconstructed safely from the event timeline. Do not merge final-match
    xG/shots/corners into a minute-level row: that would leak the future.
    """
    period = period_for_minute(minute)
    home_score, away_score = score_as_of(match.goals, minute)

    another_goal = int(any(goal.minute > minute for goal in match.goals))

    goal_before_ht: int | None = None
    two_plus_goals_second_half: int | None = None
    if period == 1:
        goal_before_ht = int(
            any(goal.period == 1 and goal.minute > minute for goal in match.goals)
        )
        two_plus_goals_second_half = int(
            sum(1 for goal in match.goals if goal.period == 2) >= 2
        )

    return ArchiveTrainingExample(
        match_id=match.match_id,
        kickoff_at=match.kickoff_at,
        minute=minute,
        period=period,
        home_score=home_score,
        away_score=away_score,
        another_goal=another_goal,
        goal_before_ht=goal_before_ht,
        two_plus_goals_second_half=two_plus_goals_second_half,
    )


def build_archive_examples(
    match: ArchiveMatch,
    cutoffs: Iterable[float] | None = None,
) -> list[ArchiveTrainingExample]:
    """Create many supervised rows from one finished historical match.

    With the default minute grid, one archived match can contribute up to 90
    time-local examples instead of only one final-result row.
    """
    if cutoffs is None:
        cutoffs = range(1, 91)
    return [build_archive_example(match, float(minute)) for minute in cutoffs]
