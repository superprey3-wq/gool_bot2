from __future__ import annotations

import json
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

from gool_bot2 import runtime_fastlane as fastlane
from gool_bot2 import xbet_market_pressure as xbet


def _record(mid: str, *, age_seconds: float = 0.0, minute: int = 20) -> dict:
    captured = (datetime.now(timezone.utc) - timedelta(seconds=age_seconds)).isoformat()
    return {
        "captured_at": captured,
        "match": {
            "flashscore_event_id": mid,
            "home": f"Home {mid}",
            "away": f"Away {mid}",
            "minute": minute,
            "home_score": 0,
            "away_score": 0,
            "is_finished": False,
            "is_halftime": False,
        },
        "providers": {"flashscore": {"stats": {}, "meta": {}}},
        "prefilter": {"candidate": False, "score": 0.0, "reasons": []},
    }


class _RunWorker:
    def __init__(self) -> None:
        self._offsets: dict[str, int] = {}
        self.calls: list[str] = []

    def _process(self, record: dict) -> int:
        self.calls.append(str((record.get("match") or {}).get("flashscore_event_id") or ""))
        return 1


def test_cold_start_skips_old_raw_snapshots_and_keeps_current_live(tmp_path: Path, monkeypatch, capsys):
    raw = tmp_path / "raw"
    raw.mkdir()
    path = raw / "2026-09-06-12.jsonl"
    path.write_text(
        json.dumps(_record("stale", age_seconds=900)) + "\n"
        + json.dumps(_record("fresh", age_seconds=2)) + "\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("RUNTIME_DATA_DIR", str(tmp_path / "runtime"))
    monkeypatch.setenv("SIGNAL_FRESH_RAW_MAX_AGE_SECONDS", "300")
    monkeypatch.setattr(xbet, "load_market_state", lambda path=None: {"matches": {}})

    worker = _RunWorker()
    worker._runtime_last_market_recheck = time.monotonic()
    emitted = fastlane._fresh_run_once(worker, raw)

    assert emitted == 1
    assert worker.calls == ["fresh"]
    assert worker._offsets[str(path)] == path.stat().st_size
    output = capsys.readouterr().out
    assert "SIGNAL_COLD_START_SKIP stale_rows=1" in output
    health = json.loads((tmp_path / "runtime" / "live" / "signal_worker_health.json").read_text("utf-8"))
    assert health["status"] == "idle"
    assert health["processed"] == 1
    assert health["stale_rows_skipped"] == 1


def test_active_live_only_fast_path_never_calls_legacy_analyzer(monkeypatch):
    from gool_bot2 import storage_market_signal_worker_var as app

    monkeypatch.setenv("GOOL_MULTI_TELEGRAM_MODE", "active")
    monkeypatch.setenv("GOOL_LIVE_ONLY", "1")

    seen: list[tuple[str, bool]] = []

    class Worker:
        def __init__(self) -> None:
            self._runtime_model_by_match = {}
            self._prematch_store_disk = None

        def _attach_momentum(self, record: dict, mid: str) -> None:
            record["live_momentum"] = {"attached": mid}

    def legacy_should_not_run(self, record):
        raise AssertionError("legacy analyzer must be skipped in active LIVE_ONLY")

    monkeypatch.setattr(fastlane, "_ORIGINAL_SAFE_PROCESS", legacy_should_not_run)
    monkeypatch.setattr(
        app,
        "observe_multi_shadow",
        lambda worker, record: seen.append(("multi", bool((record.get("live_momentum") or {}).get("attached")))),
    )
    monkeypatch.setattr(app, "maybe_emit_money_flow", lambda record: seen.append(("flow", True)))

    worker = Worker()
    result = fastlane._fast_process(worker, _record("m1"))

    assert result == 0
    assert seen == [("multi", True), ("flow", True)]
    assert worker._diag_model_result == {}
    assert worker._runtime_model_by_match["m1"] == {}


def test_hybrid_or_shadow_still_uses_original_safe_process(monkeypatch):
    monkeypatch.setenv("GOOL_MULTI_TELEGRAM_MODE", "shadow")
    monkeypatch.setenv("GOOL_LIVE_ONLY", "1")

    called: list[str] = []

    def original(self, record):
        called.append(str((record.get("match") or {}).get("flashscore_event_id") or ""))
        return 3

    monkeypatch.setattr(fastlane, "_ORIGINAL_SAFE_PROCESS", original)
    assert fastlane._fast_process(object(), _record("shadow")) == 3
    assert called == ["shadow"]
