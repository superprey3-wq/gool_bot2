from __future__ import annotations

from types import SimpleNamespace

from gool_bot2 import matchbook_pagination
from gool_bot2.multi_money_flow import evaluate_money_flow
from gool_bot2.storage_live_collector import StorageLiveSnapshotCollector


def _record(*, minute: int = 60, last_goal: int | None = None, delta: float = 400.0, pp: float = 2.8):
    timeline = [] if last_goal is None else [{"minute": last_goal, "event_type": "goal", "score": [1, 1]}]
    return {
        "match": {
            "flashscore_event_id": "abc12345",
            "home": "Arsenal",
            "away": "Chelsea",
            "league": "Premier League",
            "minute": minute,
            "home_score": 1,
            "away_score": 1,
        },
        "providers": {"flashscore": {"meta": {"goal_timeline": timeline}}},
        "matchbook_exchange": {
            "available": True,
            "event": {"id": "mb1"},
            "systems": {
                "another_goal": {
                    "available": True,
                    "liquid": True,
                    "period": "FT",
                    "line": 2.5,
                    "market_id": "m1",
                    "market_name": "Total Goals 2.5",
                    "market_status": "open",
                    "volume": 2000.0,
                    "fair_over": 0.62,
                    "over": {
                        "best_back": {"odds": 1.70, "available": 300.0},
                        "best_lay": {"odds": 1.72, "available": 250.0},
                    },
                    "under": {
                        "best_back": {"odds": 2.40, "available": 200.0},
                        "best_lay": {"odds": 2.44, "available": 200.0},
                    },
                    "flow": {
                        "window_ready_30s": True,
                        "window_ready_60s": True,
                        "volume_delta_30s": delta,
                        "volume_delta_60s": delta + 100.0,
                        "fair_over_delta_pp_30s": pp,
                        "fair_over_delta_pp_60s": pp + 0.2,
                    },
                }
            },
        },
    }


def test_heavy_money_flow_is_independent_candidate(monkeypatch):
    monkeypatch.setenv("MATCHBOOK_FLOW_BET_MIN_DELTA_30S", "250")
    monkeypatch.setenv("MATCHBOOK_FLOW_BET_MIN_RELATIVE_PCT", "8")
    info = evaluate_money_flow(_record(delta=400.0, pp=2.8))
    assert info["eligible"] is True
    assert info["strategy"] == "money_flow"
    assert info["window"] == "30s"
    assert info["level"] == "HEAVY_FLOW"
    assert info["odd"] == 1.70


def test_money_flow_does_not_chase_fresh_goal():
    info = evaluate_money_flow(_record(minute=60, last_goal=59, delta=900.0, pp=5.0))
    assert info["eligible"] is False
    assert info["reason"] == "post_goal_exchange_reset"


def test_money_flow_rejects_small_or_unready_move():
    weak = evaluate_money_flow(_record(delta=40.0, pp=0.5))
    assert weak["eligible"] is False
    record = _record(delta=900.0, pp=5.0)
    flow = record["matchbook_exchange"]["systems"]["another_goal"]["flow"]
    flow["window_ready_30s"] = False
    flow["window_ready_60s"] = False
    unready = evaluate_money_flow(record)
    assert unready["eligible"] is False


def test_production_detail_windows_and_top_leagues():
    assert StorageLiveSnapshotCollector._entry_window(1)
    assert StorageLiveSnapshotCollector._entry_window(30)
    assert not StorageLiveSnapshotCollector._entry_window(31)
    assert not StorageLiveSnapshotCollector._entry_window(45)
    assert StorageLiveSnapshotCollector._entry_window(46)
    assert StorageLiveSnapshotCollector._entry_window(75)
    assert not StorageLiveSnapshotCollector._entry_window(76)
    assert StorageLiveSnapshotCollector._top_league("England: Premier League")
    assert StorageLiveSnapshotCollector._top_league("UEFA Champions League")
    assert not StorageLiveSnapshotCollector._top_league("Regional League")


def test_matchbook_pagination_collects_beyond_first_100(monkeypatch):
    monkeypatch.setenv("MATCHBOOK_EVENTS_PER_PAGE", "100")
    monkeypatch.setenv("MATCHBOOK_MAX_PAGES", "4")

    def fake_page(page: int, per_page: int):
        assert per_page == 100
        if page == 1:
            rows = [{"id": f"e{i}"} for i in range(100)]
        elif page == 2:
            rows = [{"id": f"e{i}"} for i in range(100, 120)]
        else:
            rows = []
        return {"events": rows}

    monkeypatch.setattr(matchbook_pagination, "_page_payload", fake_page)
    monkeypatch.setattr(
        matchbook_pagination,
        "decode_event",
        lambda row: {
            "event_id": row["id"],
            "home": "H",
            "away": "A",
            "start": None,
            "totals": {},
        },
    )
    rows = matchbook_pagination.fetch_events_paginated()
    assert len(rows) == 120
    assert rows[-1]["event_id"] == "e119"
