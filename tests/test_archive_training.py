from datetime import datetime, timezone

from gool_bot2.archive_training import (
    ArchiveGoalEvent,
    ArchiveMatch,
    build_archive_example,
    build_archive_examples,
)


def _match() -> ArchiveMatch:
    return ArchiveMatch(
        match_id="m1",
        kickoff_at=datetime(2026, 8, 30, 12, 0, tzinfo=timezone.utc),
        goals=(
            ArchiveGoalEvent(minute=12, period=1, side="home"),
            ArchiveGoalEvent(minute=43, period=1, side="away"),
            ArchiveGoalEvent(minute=55, period=2, side="home"),
            ArchiveGoalEvent(minute=78, period=2, side="away"),
        ),
    )


def test_first_half_cutoff_gets_all_three_targets() -> None:
    row = build_archive_example(_match(), 20)

    assert (row.home_score, row.away_score) == (1, 0)
    assert row.another_goal == 1
    assert row.goal_before_ht == 1
    assert row.two_plus_goals_second_half == 1


def test_second_half_cutoff_only_keeps_another_goal_target() -> None:
    row = build_archive_example(_match(), 60)

    assert (row.home_score, row.away_score) == (2, 1)
    assert row.another_goal == 1
    assert row.goal_before_ht is None
    assert row.two_plus_goals_second_half is None


def test_cutoff_after_last_goal_has_no_future_goal() -> None:
    row = build_archive_example(_match(), 85)
    assert row.another_goal == 0


def test_one_match_can_expand_into_many_time_local_examples() -> None:
    rows = build_archive_examples(_match(), cutoffs=[10, 20, 30, 40, 50])
    assert [row.minute for row in rows] == [10.0, 20.0, 30.0, 40.0, 50.0]
