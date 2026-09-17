from __future__ import annotations

from gool_bot2.xbet_market_worker import ScoreEpochMultiSportSteamWorker
from gool_bot2.xbet_multisport_steam import SPORTS, _metric, detect_steam


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


def test_hockey_score_change_clears_old_market_epoch(tmp_path):
    worker = ScoreEpochMultiSportSteamWorker(tmp_path)

    history, changed_at = worker._append_history(_row("hockey", (1, 1), 10.0, 4.50))
    assert len(history) == 1
    assert changed_at is None

    history, changed_at = worker._append_history(_row("hockey", (1, 1), 30.0, 4.80))
    assert len(history) == 2
    assert changed_at is None

    history, changed_at = worker._append_history(_row("hockey", (2, 1), 50.0, 5.30))

    assert len(history) == 1
    assert history[0]["score"] == [2, 1]
    assert changed_at == 50.0
    assert detect_steam(
        history,
        SPORTS["hockey"],
        now=50.0,
        score_changed_at=changed_at,
    ) is None


def test_hockey_new_signal_requires_fresh_post_goal_samples(tmp_path):
    worker = ScoreEpochMultiSportSteamWorker(tmp_path)

    worker._append_history(_row("hockey", (0, 0), 0.0, 1.0))
    worker._append_history(_row("hockey", (0, 0), 20.0, 2.0))
    worker._append_history(_row("hockey", (0, 0), 40.0, 3.0))
    history, changed_at = worker._append_history(_row("hockey", (1, 0), 60.0, 4.0))

    assert len(history) == 1
    assert changed_at == 60.0

    for index in range(1, 4):
        history, changed_at = worker._append_history(
            _row("hockey", (1, 0), 60.0 + index * 20.0, 4.0 + index)
        )

    assert len(history) == 4
    assert all(row["score"] == [1, 0] for row in history)


def test_basketball_score_change_keeps_score_normalized_history(tmp_path):
    worker = ScoreEpochMultiSportSteamWorker(tmp_path)

    history, changed_at = worker._append_history(_row("basketball", (20, 20), 0.0, 80.0))
    assert len(history) == 1
    assert changed_at is None

    history, changed_at = worker._append_history(_row("basketball", (22, 20), 12.0, 80.0))
    assert len(history) == 2
    assert changed_at == 12.0

    history, changed_at = worker._append_history(_row("basketball", (22, 23), 24.0, 80.0))
    assert len(history) == 3
    assert changed_at == 24.0
    assert [row["score"] for row in history] == [[20, 20], [22, 20], [22, 23]]


def test_basketball_normal_scoring_does_not_create_fake_steam(tmp_path):
    worker = ScoreEpochMultiSportSteamWorker(tmp_path)
    cfg = SPORTS["basketball"]

    # The live total rises by exactly the points scored. Remaining expected
    # points therefore stay unchanged, which must not be treated as STEAM.
    samples = [
        ((20, 20), 160.5),
        ((22, 20), 162.5),
        ((22, 23), 165.5),
        ((25, 23), 168.5),
    ]
    history = []
    changed_at = None
    for index, (score, line) in enumerate(samples):
        metric = _metric({"line": line, "probability": 0.50}, score, cfg)
        history, changed_at = worker._append_history(
            _row("basketball", score, float(index * 12), metric)
        )

    assert len(history) == 4
    assert len({round(float(row["metric"]), 6) for row in history}) == 1
    assert detect_steam(history, cfg, now=48.0, score_changed_at=changed_at) is None
