from gool_bot2.archive_dataset import record_to_rows


def _record():
    return {
        "match_id": "m1",
        "kickoff_at": "2026-08-30T12:00:00+00:00",
        "home": "Home",
        "away": "Away",
        "league": "L",
        "goals": [
            {"minute": 12, "period": 1, "side": "home"},
            {"minute": 43, "period": 1, "side": "away"},
            {"minute": 55, "period": 2, "side": "home"},
            {"minute": 78, "period": 2, "side": "away"},
        ],
    }


def test_archive_row_contains_only_past_event_features_and_future_labels():
    row = record_to_rows(_record(), cutoffs=range(20, 21))[0]
    assert row["home_score"] == 1
    assert row["away_score"] == 0
    assert row["goals_last_10m"] == 1.0
    assert row["minutes_since_last_goal"] == 8.0
    assert row["future_goals_count"] == 3.0
    assert row["future_first_half_goals"] == 1.0
    assert row["second_half_goals_total"] == 2.0
    assert row["another_goal"] == 1
    assert row["goal_before_ht"] == 1
    assert row["two_plus_goals_second_half"] == 1


def test_second_half_count_labels_not_used_for_prematch_head_after_2h_starts():
    row = record_to_rows(_record(), cutoffs=range(60, 61))[0]
    assert row["future_goals_count"] == 1.0
    assert row["future_first_half_goals"] is None
    assert row["second_half_goals_total"] is None
    assert row["goal_before_ht"] is None
    assert row["two_plus_goals_second_half"] is None
