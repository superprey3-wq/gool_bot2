from gool_bot2.targets import GoalEvent, build_targets


def test_three_targets_from_first_half_snapshot():
    goals = [
        GoalEvent(minute=39, period=1),
        GoalEvent(minute=63, period=2),
        GoalEvent(minute=81, period=2),
    ]
    t = build_targets(snapshot_minute=27, snapshot_period=1, goals=goals)
    assert t.another_goal == 1
    assert t.goal_before_ht == 1
    assert t.two_plus_goals_second_half == 1


def test_first_half_goal_head_not_applicable_after_ht():
    goals = [GoalEvent(minute=70, period=2)]
    t = build_targets(snapshot_minute=55, snapshot_period=2, goals=goals)
    assert t.another_goal == 1
    assert t.goal_before_ht is None


def test_complete_second_half_target_not_used_after_second_half_starts():
    goals = [GoalEvent(minute=50, period=2), GoalEvent(minute=70, period=2)]
    t = build_targets(snapshot_minute=55, snapshot_period=2, goals=goals)
    assert t.two_plus_goals_second_half is None
