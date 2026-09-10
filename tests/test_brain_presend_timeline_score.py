from __future__ import annotations

from types import SimpleNamespace

import gool_bot2.brain_card_restore as card
import gool_bot2.stale_replay_guard as stale


def test_presend_uses_confirmed_timeline_when_master_score_lags(monkeypatch):
    class FakeFlashscore:
        def event_states(self, ids):
            return {
                "fs123456": {
                    "event_id": "fs123456",
                    "coarse_status": "2",
                    "status_code": "13",
                    "is_live": True,
                    "is_finished": False,
                    "home_score": 0,
                    "away_score": 2,
                }
            }

        def fetch_goal_timeline(self, event_id):
            return [
                {"minute": 22, "event_type": "goal", "score": [0, 1]},
                {"minute": 59, "event_type": "goal", "score": [0, 2]},
                {"minute": 67, "event_type": "goal", "score": [0, 3]},
            ]

    monkeypatch.setattr("gool_bot2.providers.flashscore.FlashscoreProvider", FakeFlashscore)

    state = card._fresh_flashscore_state("fs123456")
    assert state is not None
    assert (state["home_score"], state["away_score"]) == (0, 3)
    assert state["score_source"] == "goal_timeline"

    reason = stale._strict_pre_send_reason(
        {"match": {"flashscore_event_id": "fs123456", "home_score": 0, "away_score": 2}},
        SimpleNamespace(score=(0, 2)),
        {"match_id": "fs123456", "score": [0, 2]},
    )
    assert reason == "score_changed_0-2_to_0-3"


def test_presend_fails_closed_when_master_and_timeline_conflict(monkeypatch):
    class FakeFlashscore:
        def event_states(self, ids):
            return {
                "fs123456": {
                    "event_id": "fs123456",
                    "coarse_status": "2",
                    "is_live": True,
                    "is_finished": False,
                    "home_score": 1,
                    "away_score": 1,
                }
            }

        def fetch_goal_timeline(self, event_id):
            return [
                {"minute": 10, "event_type": "goal", "score": [0, 1]},
                {"minute": 20, "event_type": "goal", "score": [0, 2]},
            ]

    monkeypatch.setattr("gool_bot2.providers.flashscore.FlashscoreProvider", FakeFlashscore)

    reason = stale._strict_pre_send_reason(
        {"match": {"flashscore_event_id": "fs123456", "home_score": 1, "away_score": 1}},
        SimpleNamespace(score=(1, 1)),
        {"match_id": "fs123456", "score": [1, 1]},
    )
    assert reason == "flashscore_master_timeline_score_conflict"
