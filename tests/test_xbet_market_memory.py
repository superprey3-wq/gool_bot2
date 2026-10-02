from __future__ import annotations

import json
from pathlib import Path

from gool_bot2.xbet_market_memory import delete_market_memory, record_market_snapshot


def _markets(over: float, under: float) -> dict:
    return {
        "match_1x2": {"home": 1.80, "draw": 3.60, "away": 4.40},
        "btts": {"yes": 1.75, "no": 2.05},
        "match_total": [{"line": 2.5, "over": over, "under": under}],
        "home_total": [],
        "away_total": [],
        "first_half_total": [{"line": 0.5, "over": 1.45, "under": 2.55}],
    }


def test_market_memory_tracks_open_close_and_live(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("RUNTIME_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("XBET_MARKET_MEMORY_PREMATCH_HEARTBEAT_SECONDS", "0")
    monkeypatch.setenv("XBET_MARKET_MEMORY_LIVE_HEARTBEAT_SECONDS", "0")

    assert record_market_snapshot(
        event_id="xb1", home="Germany", away="Serbia", phase="PREMATCH",
        markets=_markets(1.95, 1.85), captured_at="2026-10-02T10:00:00+00:00",
        scheduled_start_ts=1790935200.0,
    )
    assert record_market_snapshot(
        event_id="xb1", home="Germany", away="Serbia", phase="PREMATCH",
        markets=_markets(1.70, 2.10), captured_at="2026-10-02T14:55:00+00:00",
        scheduled_start_ts=1790935200.0,
    )
    assert record_market_snapshot(
        event_id="xb1", home="Germany", away="Serbia", phase="LIVE",
        markets=_markets(1.82, 1.95), captured_at="2026-10-02T15:05:00+00:00",
        minute=5, score_home=0, score_away=0,
    )

    state = json.loads((tmp_path / "live" / "market_memory_state.json").read_text("utf-8"))
    row = state["matches"]["xb1"]
    key = "match_total:2.5:over"
    assert row["opening"][key] == 1.95
    assert row["closing"][key] == 1.70
    assert row["current"][key] == 1.82
    assert row["phase"] == "LIVE"
    assert row["movement"][key]["implied_change_pp"] != 0
    assert row["recent_movement"][key]["5m"]["from"] == 1.70


def test_ephemeral_market_memory_deletes_finished_match(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("RUNTIME_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("XBET_MARKET_MEMORY_EPHEMERAL", "1")
    monkeypatch.setenv("XBET_MARKET_MEMORY_PREMATCH_HEARTBEAT_SECONDS", "0")
    monkeypatch.setenv("XBET_MARKET_MEMORY_LIVE_HEARTBEAT_SECONDS", "0")

    assert record_market_snapshot(
        event_id="xb2", home="A", away="B", phase="PREMATCH",
        markets=_markets(1.90, 1.90), captured_at="2026-10-02T09:00:00+00:00",
        scheduled_start_ts=1790935200.0,
    )
    assert record_market_snapshot(
        event_id="xb2", home="A", away="B", phase="LIVE",
        markets=_markets(1.70, 2.10), captured_at="2026-10-02T15:10:00+00:00",
        minute=10, score_home=0, score_away=0, flashscore_event_id="fs2",
    )

    active = tmp_path / "live" / "market_memory" / "active" / "xb2.jsonl"
    assert active.exists()
    assert delete_market_memory("xb2") is True
    assert not active.exists()

    state = json.loads((tmp_path / "live" / "market_memory_state.json").read_text("utf-8"))
    assert "xb2" not in state.get("matches", {})
