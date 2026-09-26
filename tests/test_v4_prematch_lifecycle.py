from gool_bot2.v4_prematch_lifecycle import attach_live_state_to_prematch


def test_prematch_row_becomes_in_game_without_duplicate():
    rows = [{
        "origin": "prematch", "result": "pending", "event_id": "xb1",
        "home": "Arsenal", "away": "Chelsea", "odd": 1.85,
    }]
    live = [{"match": {
        "flashscore_event_id": "fs1", "home": "Arsenal FC", "away": "Chelsea",
        "minute": 7, "home_score": 0, "away_score": 0,
    }}]
    assert attach_live_state_to_prematch(rows, live) == 1
    assert len(rows) == 1
    assert rows[0]["match_id"] == "fs1"
    assert rows[0]["lifecycle"] == "in_game"
    assert rows[0]["current_minute"] == 7


def test_unrelated_live_match_is_not_attached():
    rows = [{"origin": "prematch", "result": "pending", "home": "Arsenal", "away": "Chelsea"}]
    live = [{"match": {
        "flashscore_event_id": "x", "home": "Milan", "away": "Inter", "minute": 12,
    }}]
    assert attach_live_state_to_prematch(rows, live) == 0
    assert not rows[0].get("match_id")
