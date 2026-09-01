from gool_bot2.signal_policy import market_state_gate, time_gate


def test_another_goal_accepts_any_score_state():
    for score in ((0, 0), (1, 0), (2, 0), (2, 2), (4, 3)):
        assert market_state_gate("another_goal", *score).allowed is True


def test_another_goal_live_windows():
    assert time_gate("another_goal", 9).allowed is False
    assert time_gate("another_goal", 10).allowed is True
    assert time_gate("another_goal", 50).allowed is False
    assert time_gate("another_goal", 55).allowed is True
    assert time_gate("another_goal", 75).allowed is True
    assert time_gate("another_goal", 76).allowed is False


def test_disabled_legacy_market_intrinsics_remain_consistent():
    # These heads are not emitted as new signals anymore, but their low-level
    # market helpers remain for historical journal/model compatibility.
    assert market_state_gate("goal_before_ht", 1, 0).allowed is True
    assert market_state_gate("over_2_5", 1, 0).allowed is True
    assert market_state_gate("over_2_5", 0, 1).allowed is True
    assert market_state_gate("over_2_5", 1, 1).allowed is False
    assert market_state_gate("both_teams_to_score", 2, 0).allowed is True
    assert market_state_gate("both_teams_to_score", 1, 1).allowed is False
