from __future__ import annotations

import json
import time

import pytest

from gool_bot2.browser_context_store import attach_browser_context, context_for_record
from gool_bot2.match_context import provider_count, provider_pair


def _record(*, minute: int = 68, score: tuple[int, int] = (1, 1)) -> dict:
    return {
        "match": {
            "flashscore_event_id": "fs-1",
            "home": "Home",
            "away": "Away",
            "minute": minute,
            "home_score": score[0],
            "away_score": score[1],
        },
        "providers": {
            "flashscore": {"stats": {"xg": [0.4, 0.3]}},
        },
    }


def _write(path, *, minute: int = 68, score=(1, 1), age: float = 0.0) -> None:
    path.write_text(
        json.dumps({
            "matches": {
                "fs-1": {
                    "captured_epoch": time.time() - age,
                    "minute": minute,
                    "score": list(score),
                    "scores365_game_id": "365-1",
                    "stats": {"xg": [0.8, 0.7], "shots_on_target": [5, 4]},
                    "trends": [{"text": "Over 2.5 Goals - 8/10 Last Matches", "percentage": 0.8}],
                }
            }
        }),
        "utf-8",
    )


def test_fresh_browser_context_attaches(monkeypatch, tmp_path):
    path = tmp_path / "browser.json"
    _write(path)
    monkeypatch.setenv("GOOL_BROWSER_CONTEXT_PATH", str(path))
    monkeypatch.setenv("GOOL_BROWSER_CONTEXT_TTL_SECONDS", "180")
    monkeypatch.setenv("GOOL_BROWSER_MAX_MINUTE_LAG", "3")

    record = _record()
    row = attach_browser_context(record)

    assert row is not None
    assert record["providers"]["browser365"]["stats"]["xg"] == [0.8, 0.7]
    assert record["browser_context"]["scores365_game_id"] == "365-1"


def test_stale_or_wrong_score_browser_context_is_rejected(monkeypatch, tmp_path):
    stale = tmp_path / "stale.json"
    _write(stale, age=600)
    monkeypatch.setenv("GOOL_BROWSER_CONTEXT_PATH", str(stale))
    monkeypatch.setenv("GOOL_BROWSER_CONTEXT_TTL_SECONDS", "180")
    assert context_for_record(_record()) is None

    wrong = tmp_path / "wrong.json"
    _write(wrong, score=(2, 1))
    monkeypatch.setenv("GOOL_BROWSER_CONTEXT_PATH", str(wrong))
    assert context_for_record(_record(score=(1, 1))) is None


def test_browser365_is_only_sparse_provider_fallback():
    healthy = _record()
    healthy["providers"]["fotmob"] = {"stats": {"xg": [0.6, 0.5]}}
    healthy["providers"]["browser365"] = {"stats": {"xg": [2.0, 2.0]}}

    # Healthy two-source consensus ignores browser365 because it observes the
    # same 365Scores data through another transport and is not independent.
    healthy_pair = provider_pair(healthy, "xg")
    assert healthy_pair == pytest.approx((0.5, 0.4))
    assert provider_count(healthy, "xg") == 2

    sparse = _record()
    sparse["providers"]["browser365"] = {"stats": {"xg": [0.8, 0.7]}}
    sparse_pair = provider_pair(sparse, "xg")
    assert sparse_pair == pytest.approx((0.6, 0.5))
    assert provider_count(sparse, "xg") == 2
