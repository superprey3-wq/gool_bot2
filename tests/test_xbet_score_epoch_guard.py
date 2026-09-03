from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from gool_bot2.xbet_score_epoch_guard import _collect_once_score_safe, extract_xbet_score


def _game(score=(1, 0)):
    return {"SC": {"FS": {"S1": score[0], "S2": score[1]}}, "GE": []}


def _collector(tmp_path: Path, fs_score=(0, 0), xbet_score=(1, 0), red_cards=(0, 0)):
    fs = SimpleNamespace(provider_match_id="m1", home="Home", away="Away", minute=45, home_score=fs_score[0], away_score=fs_score[1])
    red_feed = f"SD÷24¬SH÷{red_cards[0]}¬SI÷{red_cards[1]}"
    obj = SimpleNamespace()
    obj.flashscore = SimpleNamespace(live_matches=lambda: [fs], _feed=lambda _path: red_feed)
    obj._fetch_index = lambda: ("root", [{"event_id": "x1", "home": "Home", "away": "Away"}])
    obj._map = lambda _fs, _candidates: "x1"
    obj._game = lambda _event_id: _game(xbet_score)
    obj._pressure = lambda *_args, **_kwargs: {"SHOULD_NOT_RUN": True}
    obj.snapshots = {"m1": [{"ts": 1.0, "score": [0, 0], "flat": {"match_total:1.5": {"odd": 1.625, "prob": 0.61}}}]}
    obj._xbet_last_scores = {"m1": (0, 0)}
    obj._xbet_score_changed_at = {}
    obj._xbet_last_red_cards = {"m1": (0, 0)}
    obj._xbet_event_changed_at = {}
    obj._xbet_event_reason = {}
    obj.state_path = tmp_path / "state.json"
    obj.history_path = tmp_path / "history.jsonl"
    return obj


def test_extracts_bookmaker_full_score():
    assert extract_xbet_score(_game((2, 1))) == (2, 1)
    assert extract_xbet_score({}) is None


def test_goal_seen_by_xbet_before_flashscore_blocks_repricing(tmp_path):
    obj = _collector(tmp_path, fs_score=(0, 0), xbet_score=(1, 0))
    state = _collect_once_score_safe(obj)
    row = state["matches"]["m1"]
    assert row["xbet_score_home"] == 1
    assert row["flashscore_score_home"] == 0
    assert row["score_desync"] is True
    assert row["repricing_guard"] is True
    assert row["markets"] == {}
    assert row["pressure"] == {}
    assert obj.snapshots["m1"] == []


def test_flashscore_catchup_still_waits_for_post_goal_reprice_guard(tmp_path):
    obj = _collector(tmp_path, fs_score=(1, 0), xbet_score=(1, 0))
    state = _collect_once_score_safe(obj)
    row = state["matches"]["m1"]
    assert row["score_desync"] is False
    assert row["repricing_guard"] is True
    assert row["repricing_guard_reason"] == "POST_GOAL_REPRICE"
    assert row["markets"] == {}
    assert row["pressure"] == {}
    assert obj.snapshots["m1"] == []


def test_red_card_change_resets_market_epoch_and_blocks_override(tmp_path):
    obj = _collector(tmp_path, fs_score=(1, 0), xbet_score=(1, 0), red_cards=(1, 0))
    obj._xbet_last_scores = {"m1": (1, 0)}
    state = _collect_once_score_safe(obj)
    row = state["matches"]["m1"]
    assert row["red_cards_home"] == 1
    assert row["red_cards_away"] == 0
    assert row["repricing_guard"] is True
    assert row["repricing_guard_reason"] == "EVENT_REPRICE_RED_CARD_HOME"
    assert row["markets"] == {}
    assert row["pressure"] == {}
    assert obj.snapshots["m1"] == []
