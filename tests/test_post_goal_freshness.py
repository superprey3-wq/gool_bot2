from gool_bot2.post_goal_freshness import post_goal_freshness


def test_blocks_pressure_window_that_still_contains_pre_goal_data(monkeypatch):
    monkeypatch.setenv("GOOL_POST_GOAL_FRESH_MINUTES", "5")
    record = {
        "match": {"minute": 21, "last_goal_minute": 18},
        "live_analysis": {"recent_5m": {"start_minute": 16, "end_minute": 21}},
    }
    ok, reason = post_goal_freshness(record)
    assert not ok
    assert reason.startswith("post_goal_freshness_")


def test_blocks_even_after_clock_cooldown_if_window_crosses_goal(monkeypatch):
    monkeypatch.setenv("GOOL_POST_GOAL_FRESH_MINUTES", "5")
    record = {
        "match": {"minute": 25, "last_goal_minute": 19},
        "live_analysis": {"recent_5m": {"start_minute": 18, "end_minute": 25}},
    }
    ok, reason = post_goal_freshness(record)
    assert not ok
    assert reason == "post_goal_window_contains_pre_goal"


def test_allows_new_window_and_post_goal_snapshots(monkeypatch):
    monkeypatch.setenv("GOOL_POST_GOAL_FRESH_MINUTES", "5")
    monkeypatch.setenv("GOOL_POST_GOAL_MIN_SNAPSHOTS", "2")
    record = {
        "match": {"minute": 25, "last_goal_minute": 19},
        "live_analysis": {"recent_5m": {"start_minute": 20, "end_minute": 25}},
        "snapshots": [{"minute": 20}, {"minute": 22}, {"minute": 25}],
    }
    assert post_goal_freshness(record) == (True, "")


def test_no_goal_does_not_block_initial_signal():
    assert post_goal_freshness({"match": {"minute": 12}}) == (True, "")
