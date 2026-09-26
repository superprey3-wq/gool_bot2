from pathlib import Path

from gool_bot2.journal import load_signal_journal
from gool_bot2.v4_prematch_delivery import (
    append_prematch_entry,
    mark_prematch_in_game,
    parlay_leg_states,
    prematch_keyboard,
)


def test_prematch_entry_is_same_journal_row_when_entered(tmp_path):
    path = tmp_path / "journal.json"
    entry = append_prematch_entry(path, {
        "event_id": "e1", "home": "A", "away": "B", "market": "ТБ 2.5", "odd": 1.8,
    })
    assert mark_prematch_in_game(path, entry["entry_id"], chat_id=123)
    rows = load_signal_journal(path)
    assert len(rows) == 1
    assert rows[0]["origin"] == "prematch"
    assert rows[0]["in_game"] is True
    assert rows[0]["entered_by_chat_id"] == "123"


def test_prematch_keyboard_uses_v4_callback():
    data = prematch_keyboard("abc")["inline_keyboard"][0][0]
    assert data["callback_data"] == "v4ig:abc"
    assert "В игре" in data["text"]


def test_parlay_legs_keep_individual_live_states():
    parlay = {"legs": [{"event_id": "a"}, {"event_id": "b"}]}
    live = [{"event_id": "a", "lifecycle": "in_game", "current_minute": 22, "current_score": [1, 0]}]
    states = parlay_leg_states(parlay, live)
    assert states[0]["lifecycle"] == "in_game"
    assert states[0]["current_minute"] == 22
    assert states[1].get("lifecycle") is None
