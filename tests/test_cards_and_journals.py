from pathlib import Path

from gool_bot2.archive_dataset import record_to_rows
from gool_bot2.archive_statsbomb import record_from_statsbomb
from gool_bot2.foundation_inference import live_foundation_features
from gool_bot2.journal import load_signal_journal, mark_in_game, save_signal_journal
from gool_bot2.match_context import card_context


def test_statsbomb_cards_are_cutoff_safe_features():
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
        {"period": 1, "minute": 12, "second": 0, "team": {"id": 1}, "type": {"name": "Foul Committed"}, "foul_committed": {"card": {"name": "Yellow Card"}}},
        {"period": 1, "minute": 30, "second": 0, "team": {"id": 2}, "type": {"name": "Bad Behaviour"}, "bad_behaviour": {"card": {"name": "Red Card"}}},
        {"period": 1, "minute": 40, "second": 0, "team": {"id": 1}, "type": {"name": "Shot"}, "shot": {"statsbomb_xg": 0.2, "outcome": {"name": "Goal"}}},
    ]
    record = record_from_statsbomb(match, events)
    row_20 = record_to_rows(record, cutoffs=range(20, 21))[0]
    row_35 = record_to_rows(record, cutoffs=range(35, 36))[0]
    assert row_20["home_yellow_cards"] == 1.0
    assert row_20["away_red_cards"] == 0.0
    assert row_35["away_red_cards"] == 1.0


def test_live_foundation_features_include_cards_and_current_stats():
    record = {
        "match": {"minute": 25, "home_score": 0, "away_score": 0, "is_halftime": False},
        "providers": {
            "flashscore": {
                "stats": {
                    "shots": (4.0, 2.0),
                    "shots_on_target": (2.0, 1.0),
                    "xg": (0.55, 0.20),
                    "corners": (3.0, 1.0),
                    "yellow_cards": (2.0, 1.0),
                },
                "meta": {"goal_timeline": []},
            },
            "365scores": {"stats": {"red_cards": (0.0, 1.0)}, "meta": {}},
        },
    }
    frame = live_foundation_features(record)
    row = frame.iloc[0]
    assert row["home_shots"] == 4.0
    assert row["home_xg"] == 0.55
    assert row["home_yellow_cards"] == 2.0
    assert row["away_red_cards"] == 1.0
    cards = card_context(record)
    assert cards["home_yellow"] == 2
    assert cards["away_red"] == 1
    assert cards["has_red_card"] is True


def test_in_game_button_state_is_persisted(tmp_path: Path):
    path = tmp_path / "signals.json"
    save_signal_journal(
        path,
        [{"match_id": "m1", "head": "another_goal", "result": "pending", "in_game": False}],
    )
    assert mark_in_game(path, "m1", "another_goal", chat_id="42") is True
    row = load_signal_journal(path)[0]
    assert row["in_game"] is True
    assert row["entered_by_chat_id"] == "42"
    assert row.get("entered_at")
