from gool_bot2.signal_policy import market_state_gate


def test_goal_before_ht_is_zero_zero_only():
    assert market_state_gate("goal_before_ht", 0, 0).allowed is True
    result = market_state_gate("goal_before_ht", 1, 0)
    assert result.allowed is False
    assert "first_half_zero_zero_only" in result.reasons


def test_btts_not_emitted_after_both_teams_already_scored():
    assert market_state_gate("both_teams_to_score", 2, 0).allowed is True
    result = market_state_gate("both_teams_to_score", 1, 1)
    assert result.allowed is False
    assert "btts_already_won" in result.reasons


def test_over25_not_emitted_after_three_goals():
    assert market_state_gate("over_2_5", 1, 1).allowed is True
    assert market_state_gate("over_2_5", 2, 0).allowed is True
    result = market_state_gate("over_2_5", 2, 1)
    assert result.allowed is False
    assert "over25_already_won" in result.reasons


def test_another_goal_accepts_any_score_state():
    for score in ((0, 0), (1, 0), (2, 0), (2, 2), (4, 3)):
        assert market_state_gate("another_goal", *score).allowed is True
