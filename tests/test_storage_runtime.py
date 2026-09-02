from __future__ import annotations

import json
import os
import time
from pathlib import Path

from gool_bot2.journal import append_analysis
from gool_bot2.storage_runtime import PrematchStore, hydrate_prematch, startup_cleanup, trim_file_tail


def _record(mid: str, ctx: dict | None = None) -> dict:
    row = {"match": {"flashscore_event_id": mid, "minute": 25, "is_finished": False}}
    if ctx is not None:
        row["prematch_context"] = ctx
    return row


def _ctx() -> dict:
    rows = [{"home": "A", "away": "B", "home_score": 2, "away_score": 1} for _ in range(5)]
    return {"home_recent": rows, "away_recent": rows, "home_at_home": rows, "away_away": rows, "h2h": rows}


def test_prematch_store_round_trip_and_hydrate(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("PREMATCH_CACHE_DIR", str(tmp_path / "prematch"))
    store = PrematchStore()
    assert store.save("abc-123", _ctx()) is True
    record = _record("abc-123")
    assert hydrate_prematch(record, store) is True
    assert len(record["prematch_context"]["home_recent"]) == 5
    store.delete("abc-123")
    assert store.load("abc-123") == {}


def test_trim_file_tail_keeps_complete_lines(tmp_path: Path):
    path = tmp_path / "rows.jsonl"
    lines = [json.dumps({"n": i}) for i in range(100)]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    before = path.stat().st_size
    freed = trim_file_tail(path, 256)
    assert freed > 0
    assert path.stat().st_size < before
    parsed = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
    assert parsed
    assert parsed[-1]["n"] == 99


def test_analysis_is_bounded(tmp_path: Path, monkeypatch):
    path = tmp_path / "analysis.jsonl"
    monkeypatch.setenv("ANALYSIS_MAX_BYTES", str(256 * 1024))
    monkeypatch.setenv("ANALYSIS_KEEP_BYTES", str(128 * 1024))
    payload = "x" * 3000
    for i in range(140):
        append_analysis(path, {"i": i, "payload": payload})
    assert path.stat().st_size < 256 * 1024 + 10 * 1024
    last = json.loads(path.read_text(encoding="utf-8").splitlines()[-1])
    assert last["i"] == 139


def test_startup_cleanup_purges_restart_archives_and_old_cards(tmp_path: Path, monkeypatch):
    runtime = tmp_path / "runtime"
    raw = runtime / "raw" / "live"
    archive = runtime / "raw" / "archive" / "20260902T120000Z"
    cards = runtime / "live" / "shadow_cards"
    raw.mkdir(parents=True)
    archive.mkdir(parents=True)
    cards.mkdir(parents=True)
    (archive / "old.jsonl").write_bytes(b"x" * 4096)
    (cards / "old.png").write_bytes(b"x" * 2048)
    live = raw / "2026-09-02.jsonl"
    ctx = _ctx()
    rows = []
    for i in range(120):
        rows.append(json.dumps({"captured_at": "2026-09-02T12:00:00+00:00", "match": {"flashscore_event_id": "m1", "minute": i % 90 + 1}, "prematch_context": ctx, "pad": "x" * 500}, separators=(",", ":")))
    live.write_text("\n".join(rows) + "\n", encoding="utf-8")

    monkeypatch.setenv("RUNTIME_DATA_DIR", str(runtime))
    monkeypatch.setenv("RAW_LIVE_DIR", str(raw))
    monkeypatch.setenv("SHADOW_MARKET_CARDS", str(cards))
    monkeypatch.setenv("PREMATCH_CACHE_DIR", str(runtime / "live" / "prematch_cache"))
    monkeypatch.setenv("RAW_LIVE_STARTUP_MAX_BYTES", "16384")
    monkeypatch.setenv("PREMATCH_BOOTSTRAP_BYTES", "65536")
    monkeypatch.setenv("RAW_LIVE_RETENTION_MINUTES", "100000")

    result = startup_cleanup()
    assert result["archives"] == 1
    assert not archive.exists()
    assert not (cards / "old.png").exists()
    assert live.stat().st_size <= 17000
    assert PrematchStore().load("m1")
