from __future__ import annotations

import json
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

from gool_bot2 import runtime_hardening as hardening
from gool_bot2 import xbet_market_pressure as xbet


def _iso_now(delta_seconds: float = 0.0) -> str:
    return (datetime.now(timezone.utc) + timedelta(seconds=delta_seconds)).isoformat()


def _record(mid: str, minute: int = 20, score: tuple[int, int] = (0, 0)) -> dict:
    return {
        "captured_at": _iso_now(),
        "match": {
            "flashscore_event_id": mid,
            "home": f"Home {mid}",
            "away": f"Away {mid}",
            "minute": minute,
            "home_score": score[0],
            "away_score": score[1],
            "is_finished": False,
            "is_halftime": False,
        },
        "providers": {"flashscore": {"stats": {}, "meta": {}}},
        "prefilter": {"candidate": False, "score": 0.0, "reasons": []},
    }


class _DummyWorker:
    def __init__(self) -> None:
        self._offsets: dict[str, int] = {}
        self.calls: list[tuple[str, bool, int]] = []

    def _process(self, record: dict) -> int:
        match = record.get("match") or {}
        mid = str(match.get("flashscore_event_id") or "")
        if mid == "bad":
            raise RuntimeError("broken match")
        self.calls.append((mid, bool(record.get("runtime_market_recheck")), int(match.get("minute") or 0)))
        return 1


def test_bad_record_does_not_stop_following_matches_and_market_recheck_is_score_safe(tmp_path: Path, monkeypatch):
    raw = tmp_path / "raw"
    raw.mkdir()
    path = raw / "live.jsonl"
    path.write_text(
        json.dumps(_record("bad")) + "\n" + json.dumps(_record("good", minute=20, score=(1, 0))) + "\n",
        encoding="utf-8",
    )

    monkeypatch.setenv("MARKET_REEVAL_INTERVAL_SECONDS", "5")
    monkeypatch.setenv("MARKET_REEVAL_MAX_XBET_AGE_SECONDS", "30")
    monkeypatch.setenv("MARKET_REEVAL_MAX_RAW_AGE_SECONDS", "75")

    state = {
        "captured_at": _iso_now(),
        "matches": {
            "good": {
                "captured_at": _iso_now(),
                "minute": 21,
                "flashscore_score_home": 1,
                "flashscore_score_away": 0,
                "score_home": 1,
                "score_away": 0,
            }
        },
    }
    monkeypatch.setattr(xbet, "load_market_state", lambda path=None: state)

    worker = _DummyWorker()
    first = hardening.hardened_run_once(worker, raw)
    assert first == 1
    assert worker.calls == [("good", False, 20)]

    worker._runtime_last_market_recheck = 0.0
    second = hardening.hardened_run_once(worker, raw)
    assert second == 1
    assert worker.calls[-1] == ("good", True, 21)

    # A score change in the fast market collector must block replay until the
    # authoritative raw football snapshot catches up.
    state["matches"]["good"]["flashscore_score_home"] = 2
    state["matches"]["good"]["score_home"] = 2
    state["matches"]["good"]["captured_at"] = _iso_now()
    worker._runtime_last_market_recheck = 0.0
    third = hardening.hardened_run_once(worker, raw)
    assert third == 0
    assert worker.calls[-1] == ("good", True, 21)


def test_stale_matchbook_state_is_never_actionable(monkeypatch):
    original = lambda record, payload: {"available": True, "captured_at": payload.get("captured_at")}
    monkeypatch.setattr(hardening, "_ORIGINAL_MATCHBOOK_CONTEXT", original)
    monkeypatch.setenv("MATCHBOOK_STATE_MAX_AGE_SECONDS", "45")

    stale = {"captured_at": _iso_now(-120), "events": []}
    blocked = hardening._fresh_matchbook_context(_record("m1"), stale)
    assert blocked["available"] is False
    assert blocked["stale"] is True
    assert blocked["age_seconds"] >= 100

    fresh = {"captured_at": _iso_now(-2), "events": []}
    allowed = hardening._fresh_matchbook_context(_record("m1"), fresh)
    assert allowed["available"] is True
    assert allowed["stale"] is False
    assert allowed["age_seconds"] < 10


def test_money_flow_score_epoch_blocks_three_minutes_after_score_change(monkeypatch):
    hardening._FLOW_SCORE_EPOCHS.clear()
    monkeypatch.setenv("MATCHBOOK_FLOW_SCORE_EPOCH_RESET_SECONDS", "180")

    first = _record("flow", minute=52, score=(0, 0))
    assert hardening._flow_score_epoch_guard(first) is None

    after_goal = _record("flow", minute=53, score=(1, 0))
    guard = hardening._flow_score_epoch_guard(after_goal)
    assert guard is not None
    assert guard["reason"] == "post_goal_exchange_score_epoch_reset"
    assert guard["reset_seconds"] == 180

    same_epoch = _record("flow", minute=54, score=(1, 0))
    guard2 = hardening._flow_score_epoch_guard(same_epoch)
    assert guard2 is not None
    assert guard2["reason"] == "post_goal_exchange_score_epoch_reset"
    assert guard2["seconds_since_score_change"] < 180


def test_lazy_365_history_starts_in_background(monkeypatch):
    from gool_bot2 import prematch_goal_profile as prematch

    key = "async-365"
    hardening._HALF_FUTURES.pop(key, None)
    prematch._HALF_CONTEXT_CACHE.pop(key, None)

    class SlowProvider:
        def half_prematch_context(self, home, away, limit=6):
            time.sleep(0.20)
            return None

    monkeypatch.setattr(prematch, "_provider", lambda: SlowProvider())
    record = _record(key, minute=20, score=(0, 0))
    record["match"]["home"] = "A"
    record["match"]["away"] = "B"
    experts = {"goal_before_ht": {"state": "PASS", "probability": 0.75}}
    profile = prematch.build_prematch_goal_profile(record)

    started = time.monotonic()
    result = hardening._async_half_history(record, experts, profile)
    elapsed = time.monotonic() - started

    assert elapsed < 0.10
    assert result["lazy_365_pending"] is True

    future = hardening._HALF_FUTURES.get(key)
    assert future is not None
    future.result(timeout=2)
    result2 = hardening._async_half_history(record, experts, profile)
    assert result2["lazy_365_pending"] is False
