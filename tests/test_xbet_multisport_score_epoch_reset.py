from __future__ import annotations

import pytest

from gool_bot2.xbet_market_worker import ScoreEpochMultiSportSteamWorker
from gool_bot2.xbet_multisport_steam import SPORTS, detect_steam


def _row(sport: str, score: tuple[int, int], ts: float, metric: float) -> dict:
    return {
        "sport": sport,
        "event_id": "event-1",
        "score": [score[0], score[1]],
        "period": "LIVE",
        "ts": ts,
        "metric": metric,
        "probability": 0.52,
        "line": 5.5 if sport == "hockey" else 180.5,
        "over": 1.82,
    }


@pytest.mark.parametrize("sport", ["hockey", "basketball"])
def test_score_change_clears_old_market_epoch(tmp_path, sport: str):
    worker = ScoreEpochMultiSportSteamWorker(tmp_path)

    history, changed_at = worker._append_history(_row(sport, (1, 1), 10.0, 4.50))
    assert len(history) == 1
    assert changed_at is None

    history, changed_at = worker._append_history(_row(sport, (1, 1), 30.0, 4.80))
    assert len(history) == 2
    assert changed_at is None

    history, changed_at = worker._append_history(_row(sport, (2, 1), 50.0, 5.30))

    assert len(history) == 1
    assert history[0]["score"] == [2, 1]
    assert changed_at == 50.0
    assert detect_steam(
        history,
        SPORTS[sport],
        now=50.0,
        score_changed_at=changed_at,
    ) is None


@pytest.mark.parametrize("sport", ["hockey", "basketball"])
def test_new_signal_requires_fresh_post_score_samples(tmp_path, sport: str):
    worker = ScoreEpochMultiSportSteamWorker(tmp_path)

    worker._append_history(_row(sport, (0, 0), 0.0, 1.0))
    worker._append_history(_row(sport, (0, 0), 20.0, 2.0))
    worker._append_history(_row(sport, (0, 0), 40.0, 3.0))
    history, changed_at = worker._append_history(_row(sport, (1, 0), 60.0, 4.0))

    assert len(history) == 1
    assert changed_at == 60.0

    for index in range(1, 4):
        history, changed_at = worker._append_history(
            _row(sport, (1, 0), 60.0 + index * 20.0, 4.0 + index)
        )

    assert len(history) == 4
    assert all(row["score"] == [1, 0] for row in history)
