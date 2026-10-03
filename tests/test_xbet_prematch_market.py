from __future__ import annotations

import json

from gool_bot2 import multi_true_prematch
from gool_bot2.multi_true_prematch import apply_true_prematch_market
from gool_bot2.xbet_prematch_market import find_prematch_market


def test_find_prematch_market_matches_team_pair(tmp_path):
    path = tmp_path / "prematch.json"
    path.write_text(json.dumps({
        "matches": {
            "1": {
                "event_id": "1",
                "home": "Arsenal",
                "away": "Chelsea",
                "source": "1xbet:LineFeed",
                "match_1x2": {"home": 1.75, "draw": 4.0, "away": 5.0},
                "main_total": {"line": 2.5, "over": 1.76, "under": 2.23, "fair_over": 0.559},
            }
        }
    }), encoding="utf-8")
    row = find_prematch_market("Arsenal", "Chelsea", path=path)
    assert row is not None
    assert row["event_id"] == "1"
    assert row["match_score"] >= 0.99


def test_true_prematch_replaces_live_fallback_and_recalculates_small_prior(monkeypatch):
    monkeypatch.setattr(multi_true_prematch, "find_prematch_market", lambda home, away: {
        "event_id": "p1",
        "home": home,
        "away": away,
        "source": "1xbet:LineFeed",
        "captured_at": "2026-09-05T12:00:00+00:00",
        "match_score": 1.0,
        "match_1x2": {
            "home": 1.75,
            "draw": 4.0,
            "away": 5.0,
            "fair": {"home": 0.56, "draw": 0.24, "away": 0.20},
        },
        "main_total": {"line": 2.5, "over": 1.60, "under": 2.40, "fair_over": 0.60},
        "match_totals": [],
    })
    record = {
        "match": {"home": "Arsenal", "away": "Chelsea"},
        "match_intelligence": {
            "kickoff_market": {"quality": "late_first_observation", "usable_as_kickoff_prior": False},
            "probability_adjustment": {
                "strategy": "another_goal",
                "before": 0.78,
                "after": 0.78,
                "hazard_pp": 1.0,
                "chance_quality_pp": 0.5,
                "kickoff_total_pp": 0.0,
                "total_pp": 1.5,
            },
            "suitability": {
                "score": 0.70,
                "components": {"kickoff": 0.55},
            },
        },
    }
    experts = {"another_goal": {"probability": 0.78, "diagnostics": {}}}
    snap = apply_true_prematch_market(record, experts)
    assert snap is not None
    assert snap["quality"] == "prematch_linefeed"
    assert record["match_intelligence"]["kickoff_market"]["usable_as_kickoff_prior"] is True
    assert record["match_intelligence"]["suitability"]["components"]["kickoff"] == 0.92
    # fair_over=.60 contributes +0.8pp, so total becomes +2.3pp.
    assert record["match_intelligence"]["probability_adjustment"]["total_pp"] == 2.3
    assert experts["another_goal"]["probability"] == 0.803


def test_prematch_game_uses_current_betb2b_parameters(monkeypatch, tmp_path):
    from gool_bot2 import xbet_prematch_market as prematch

    seen = []
    monkeypatch.setattr(prematch, "_http_json", lambda url: seen.append(url) or {"Value": {"E": []}})
    collector = prematch.XBetPrematchCollector(tmp_path / "state.json")
    assert collector._game("https://1xbet.com/service-api/LineFeed", "123") == {"E": []}
    assert len(seen) == 1
    assert "grMode=4" in seen[0]
    assert "marketType=1" in seen[0]
    assert "isNewBuilder=true" in seen[0]
    assert "isSubGames=true" in seen[0]
    assert "GroupEvents=true" in seen[0]



def test_prematch_index_merges_catalog_slices(monkeypatch, tmp_path):
    from gool_bot2 import xbet_prematch_market as prematch

    monkeypatch.setattr(prematch, "INDEX_QUERIES", ("q1", "q2"))
    def fake_http(url):
        if url.endswith("q1"):
            return {"Value": [
                {"I": "1", "O1": "A", "O2": "B"},
                {"I": "2", "O1": "C", "O2": "D"},
            ]}
        if url.endswith("q2"):
            return {"Value": [
                {"I": "2", "O1": "C", "O2": "D"},
                {"I": "3", "O1": "E", "O2": "F"},
            ]}
        return {"Value": []}

    monkeypatch.setattr(prematch, "_http_json", fake_http)
    collector = prematch.XBetPrematchCollector(tmp_path / "state.json")
    root, rows = collector._index()
    assert root == prematch.ROOTS[0]
    assert {str(row["I"]) for row in rows} == {"1", "2", "3"}


def test_prematch_due_bootstraps_unseen_before_refreshing_known(monkeypatch, tmp_path):
    from gool_bot2 import xbet_prematch_market as prematch

    monkeypatch.setenv("XBET_PREMATCH_MAX_DUE_PER_CYCLE", "8")
    now = 1_800_000_000.0
    collector = prematch.XBetPrematchCollector(tmp_path / "state.json")

    def event(event_id, starts_in):
        return {
            "event_id": event_id,
            "event": {"I": event_id, "S": now + starts_in},
            "root": prematch.ROOTS[0],
        }

    rows = [
        event("known-near", 300),
        event("new-far", 3600),
    ]
    selected = collector._due_prematch_candidates(
        rows,
        now,
        known_event_ids={"known-near"},
    )
    assert [row["event_id"] for row in selected][:2] == ["new-far", "known-near"]


def test_background_tracking_does_not_truncate_catalog_to_120(monkeypatch, tmp_path):
    from gool_bot2 import xbet_prematch_market as prematch

    now = 1_800_000_000.0
    rows = [
        {"I": str(i), "O1": f"H{i}", "O2": f"A{i}", "S": now + 7200 + i}
        for i in range(180)
    ]
    collector = prematch.XBetPrematchCollector(tmp_path / "state.json")
    monkeypatch.setattr(collector, "_index", lambda: (prematch.ROOTS[0], rows))

    seen = {}
    def fake_due(indexed, current_now, *, known_event_ids=None):
        seen["count"] = len(indexed)
        return []

    monkeypatch.setattr(collector, "_due_prematch_candidates", fake_due)
    state = collector.collect_once()
    assert seen["count"] == 180
    assert state["index_events"] == 180
