from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import gool_bot2.storage_market_signal_worker_var as runtime_wrapper
from gool_bot2.signal_worker import SignalWorker


def test_signal_worker_continues_after_one_bad_record(tmp_path, monkeypatch):
    raw = tmp_path / "raw"
    raw.mkdir()
    path = raw / "live.jsonl"
    rows = [
        {"match": {"flashscore_event_id": "bad"}},
        {"match": {"flashscore_event_id": "good"}},
    ]
    path.write_text("\n".join(json.dumps(row) for row in rows) + "\n", "utf-8")
    worker = SignalWorker(tmp_path / "journal.json", analysis_path=tmp_path / "analysis.jsonl")
    seen = []

    def fake_process(record):
        mid = record["match"]["flashscore_event_id"]
        seen.append(mid)
        if mid == "bad":
            raise RuntimeError("broken provider row")
        return 1

    monkeypatch.setattr(worker, "_process", fake_process)
    assert worker.run_once(raw) == 1
    assert seen == ["bad", "good"]
    # Offset advanced beyond the bad row: next poll does not replay it forever.
    assert worker.run_once(raw) == 0


def test_market_only_wrapper_skips_legacy_and_keeps_flow_independent(monkeypatch):
    calls = []
    monkeypatch.setattr(runtime_wrapper, "_ORIG_PROCESS", lambda *_: (_ for _ in ()).throw(AssertionError("legacy must not run")))
    monkeypatch.setattr(runtime_wrapper, "refresh_late_another_goal_model", lambda *_: (_ for _ in ()).throw(AssertionError("refresh must not run")))
    monkeypatch.setattr(runtime_wrapper, "observe_multi_shadow", lambda *_: calls.append("multi"))
    monkeypatch.setattr(runtime_wrapper, "maybe_emit_money_flow", lambda *_: calls.append("flow"))

    result = runtime_wrapper._process_with_multi(
        SimpleNamespace(),
        {"runtime_scope": "market_only", "match": {"flashscore_event_id": "m1", "minute": 90}},
    )
    assert result == 0
    assert calls == ["multi", "flow"]


def test_full_wrapper_failure_domains_do_not_suppress_other_systems(monkeypatch):
    calls = []
    monkeypatch.setattr(runtime_wrapper, "_ORIG_PROCESS", lambda *_: (_ for _ in ()).throw(RuntimeError("legacy")))
    monkeypatch.setattr(runtime_wrapper, "refresh_late_another_goal_model", lambda *_: (_ for _ in ()).throw(RuntimeError("refresh")))
    monkeypatch.setattr(runtime_wrapper, "observe_multi_shadow", lambda *_: calls.append("multi"))
    monkeypatch.setattr(runtime_wrapper, "maybe_emit_money_flow", lambda *_: calls.append("flow"))

    assert runtime_wrapper._process_with_multi(SimpleNamespace(), {"runtime_scope": "full", "match": {}}) == 0
    assert calls == ["multi", "flow"]
