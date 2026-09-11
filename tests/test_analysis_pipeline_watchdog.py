from __future__ import annotations

import inspect
import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path

import gool_bot2.analysis_pipeline_guard as guard
import gool_bot2.multi_product as product


def _record() -> dict:
    return {
        "match": {
            "flashscore_event_id": "abc12345",
            "home": "Home",
            "away": "Away",
            "minute": 61,
            "home_score": 1,
            "away_score": 0,
            "is_finished": False,
        }
    }


def test_health_wrapper_marks_live_record_when_analysis_did_not_update(tmp_path, monkeypatch):
    monkeypatch.setenv("RUNTIME_DATA_DIR", str(tmp_path))
    analysis = tmp_path / "live" / "gool_multi_analysis.jsonl"
    health = tmp_path / "live" / "analysis_worker_health.json"
    analysis.parent.mkdir(parents=True, exist_ok=True)
    analysis.write_text("old\n", "utf-8")

    monkeypatch.setattr(guard, "_ORIGINAL_PROCESS", lambda self, record: 0)

    assert guard._health_wrapped_process(object(), _record()) == 0
    payload = json.loads(health.read_text("utf-8"))
    assert payload["status"] == "analysis_not_updated"
    assert payload["match_id"] == "abc12345"
    assert payload["error"] == "process_returned_without_analysis_snapshot"


def test_health_wrapper_reports_ok_when_hardened_process_writes_analysis(tmp_path, monkeypatch):
    monkeypatch.setenv("RUNTIME_DATA_DIR", str(tmp_path))
    analysis = tmp_path / "live" / "gool_multi_analysis.jsonl"
    health = tmp_path / "live" / "analysis_worker_health.json"
    analysis.parent.mkdir(parents=True, exist_ok=True)

    def fake_process(self, record):
        with analysis.open("a", encoding="utf-8") as handle:
            handle.write("fresh\n")
        return 2

    monkeypatch.setattr(guard, "_ORIGINAL_PROCESS", fake_process)

    assert guard._health_wrapped_process(object(), _record()) == 2
    payload = json.loads(health.read_text("utf-8"))
    assert payload["status"] == "ok"


def test_watchdog_detects_live_collector_with_stale_analysis(tmp_path, monkeypatch):
    monkeypatch.setenv("RUNTIME_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("GOOL_ANALYSIS_WATCHDOG_STALE_SECONDS", "90")
    monkeypatch.setenv("GOOL_ANALYSIS_WATCHDOG_STARTUP_GRACE_SECONDS", "60")
    monkeypatch.setenv("GOOL_COLLECTOR_HEALTH_MAX_AGE_SECONDS", "180")

    live = tmp_path / "live"
    live.mkdir(parents=True, exist_ok=True)
    (live / "collector_health.json").write_text(
        json.dumps(
            {
                "captured_at": datetime.now(timezone.utc).isoformat(),
                "collector": {"live": 40, "entry_window": 13, "errors": 0},
            }
        ),
        "utf-8",
    )
    analysis = live / "gool_multi_analysis.jsonl"
    analysis.write_text("old\n", "utf-8")
    old = time.time() - 300
    os.utime(analysis, (old, old))
    monkeypatch.setattr(guard, "_STARTED_MONOTONIC", time.monotonic() - 300)

    restart, info = guard.watchdog_should_restart()
    assert restart is True
    assert info["collector_live"] == 40
    assert info["analysis_stale"] is True

    text = guard.pipeline_diagnostic_text()
    assert text is not None
    assert "40" in text
    assert "analysis-worker" in text


def test_watchdog_does_not_restart_when_analysis_is_fresh(tmp_path, monkeypatch):
    monkeypatch.setenv("RUNTIME_DATA_DIR", str(tmp_path))
    live = tmp_path / "live"
    live.mkdir(parents=True, exist_ok=True)
    (live / "collector_health.json").write_text(
        json.dumps(
            {
                "captured_at": datetime.now(timezone.utc).isoformat(),
                "collector": {"live": 40},
            }
        ),
        "utf-8",
    )
    (live / "gool_multi_analysis.jsonl").write_text("fresh\n", "utf-8")
    monkeypatch.setattr(guard, "_STARTED_MONOTONIC", time.monotonic() - 300)

    restart, info = guard.watchdog_should_restart()
    assert restart is False
    assert info["analysis_stale"] is False


def test_product_installs_runtime_hardening_before_analysis_watchdog():
    source = inspect.getsource(product.install_multi_product)
    assert source.index("install_runtime_hardening()") < source.index("install_analysis_pipeline_guard()")
