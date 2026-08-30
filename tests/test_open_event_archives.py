from gool_bot2.archive_dataset import record_to_rows
from gool_bot2.archive_statsbomb import record_from_statsbomb
from gool_bot2.archive_wyscout import record_from_wyscout


def test_statsbomb_events_become_leakage_safe_features():
    match = {
        "match_id": 10,
        "match_date": "2020-01-01",
        "kick_off": "12:00:00",
        "home_team": {"home_team_id": 1, "home_team_name": "Home"},
        "away_team": {"away_team_id": 2, "away_team_name": "Away"},
        "competition": {"competition_name": "League"},
        "season": {"season_name": "2019/20"},
    }
    events = [
        {"period": 1, "minute": 10, "second": 0, "team": {"id": 1}, "type": {"name": "Shot"}, "shot": {"statsbomb_xg": 0.2, "outcome": {"name": "Saved"}}},
        {"period": 1, "minute": 20, "second": 0, "team": {"id": 1}, "type": {"name": "Shot"}, "shot": {"statsbomb_xg": 0.4, "outcome": {"name": "Goal"}}},
        {"period": 2, "minute": 60, "second": 0, "team": {"id": 2}, "type": {"name": "Shot"}, "shot": {"statsbomb_xg": 0.3, "outcome": {"name": "Goal"}}},
    ]
    record = record_from_statsbomb(match, events)
    row = record_to_rows(record, cutoffs=range(15, 16))[0]
    assert row["home_shots"] == 1.0
    assert abs(row["home_xg"] - 0.2) < 1e-9
    assert row["home_score"] == 0
    assert row["goal_before_ht"] == 1
    assert row["another_goal"] == 1


def test_wyscout_events_become_canonical_training_record():
    match = {
        "wyId": 99,
        "label": "Home - Away, 1 - 0",
        "dateutc": "2018-01-01 12:00:00",
        "competitionId": 7,
        "seasonId": 8,
        "teamsData": {"1": {"side": "home"}, "2": {"side": "away"}},
    }
    events = [
        {"matchId": 99, "teamId": 1, "matchPeriod": "1H", "eventSec": 600, "eventName": "Shot", "subEventName": "Shot", "tags": [{"id": 1801}]},
        {"matchId": 99, "teamId": 1, "matchPeriod": "1H", "eventSec": 1200, "eventName": "Shot", "subEventName": "Shot", "tags": [{"id": 101}, {"id": 1801}]},
    ]
    record = record_from_wyscout(match, events)
    row = record_to_rows(record, cutoffs=range(15, 16))[0]
    assert record["match_id"] == "wyscout:99"
    assert row["home_shots"] == 1.0
    assert row["home_shots_on_target"] == 1.0
    assert row["goal_before_ht"] == 1


def test_future_event_never_enters_snapshot_features():
    record = {
        "source": "test",
        "match_id": "t1",
        "kickoff_at": "2020-01-01T12:00:00+00:00",
        "goals": [{"minute": 40, "period": 1, "side": "home"}],
        "events": [
            {"minute": 10, "period": 1, "side": "home", "event_type": "shot", "xg": 0.1, "on_target": False},
            {"minute": 30, "period": 1, "side": "home", "event_type": "shot", "xg": 0.9, "on_target": True},
        ],
    }
    row = record_to_rows(record, cutoffs=range(20, 21))[0]
    assert row["home_shots"] == 1.0
    assert abs(row["home_xg"] - 0.1) < 1e-9
    assert row["goal_before_ht"] == 1
