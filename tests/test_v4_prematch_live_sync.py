from gool_bot2.v4_prematch_lifecycle import sync_prematch_with_live_record


def test_live_snapshot_auto_promotes_and_updates_prematch_row():
    rows = [{
        "origin": "prematch", "result": "pending", "home": "Arsenal",
        "away": "Chelsea", "event_id": "xb1", "lifecycle": "scheduled",
    }]
    live = {"match": {
        "flashscore_event_id": "fs1", "home": "Arsenal FC", "away": "Chelsea",
        "minute": 23, "home_score": 1, "away_score": 0, "is_finished": False,
    }}
    assert sync_prematch_with_live_record(rows, live) >= 1
    assert rows[0]["match_id"] == "fs1"
    assert rows[0]["lifecycle"] == "in_game"
    assert rows[0]["current_minute"] == 23
    assert rows[0]["current_score"] == [1, 0]


def test_final_snapshot_marks_waiting_settlement():
    rows = [{
        "origin": "prematch", "result": "pending", "home": "Arsenal", "away": "Chelsea",
        "match_id": "fs1", "lifecycle": "in_game",
    }]
    final = {"match": {
        "flashscore_event_id": "fs1", "home": "Arsenal", "away": "Chelsea",
        "minute": 90, "home_score": 2, "away_score": 1, "is_finished": True,
    }}
    assert sync_prematch_with_live_record(rows, final) >= 1
    assert rows[0]["current_score"] == [2, 1]
    assert rows[0]["lifecycle"] == "finished_waiting_settlement"
