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
