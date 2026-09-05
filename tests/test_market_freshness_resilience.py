from __future__ import annotations

import threading
import time
from datetime import datetime, timedelta, timezone

from gool_bot2 import matchbook_pagination, multi_money_flow, prematch_goal_profile
from gool_bot2.matchbook_exchange import matchbook_context


def _mb_record(score=(1, 1)):
    return {"match": {"flashscore_event_id": "fs1", "home": "Arsenal", "away": "Chelsea", "minute": 60, "home_score": score[0], "away_score": score[1], "is_finished": False}}


def _mb_state(captured_at: str):
    return {
        "captured_at": captured_at,
        "events": [{
            "event_id": "mb1", "name": "Arsenal vs Chelsea", "home": "Arsenal", "away": "Chelsea",
            "status": "open", "in_running": True, "volume": 5000.0, "match_odds": {},
            "totals": {"FT:2.5": {
                "id": "tot", "name": "Total Goals 2.5", "status": "open", "volume": 2000.0,
                "fair_over": 0.62,
                "over": {"best_back": {"odds": 1.70, "available": 300.0}, "best_lay": {"odds": 1.72, "available": 250.0}},
                "under": {"best_back": {"odds": 2.40, "available": 200.0}, "best_lay": {"odds": 2.44, "available": 200.0}},
                "flow": {"level": "STRONG_SUPPORT", "direction_pp": 3.2, "activity_volume": 400.0,
                         "window_ready_30s": True, "window_ready_60s": True,
                         "volume_delta_30s": 400.0, "volume_delta_60s": 600.0,
                         "fair_over_delta_pp_30s": 3.0, "fair_over_delta_pp_60s": 3.2},
            }},
        }],
    }


def test_matchbook_stale_state_is_not_actionable(monkeypatch):
    monkeypatch.setenv("MATCHBOOK_MAX_STATE_AGE_SECONDS", "45")
    stale = (datetime.now(timezone.utc) - timedelta(minutes=3)).isoformat()
    ctx = matchbook_context(_mb_record(), _mb_state(stale))
    assert ctx["available"] is False
    assert ctx["stale"] is True
    assert ctx["reason"] == "matchbook_state_stale"


def test_matchbook_fresh_state_remains_actionable(monkeypatch):
    monkeypatch.setenv("MATCHBOOK_MAX_STATE_AGE_SECONDS", "45")
    ctx = matchbook_context(_mb_record(), _mb_state(datetime.now(timezone.utc).isoformat()))
    assert ctx["available"] is True
    assert ctx["stale"] is False
    assert ctx["systems"]["money_flow"]["available"] is True


def _flow_record(score=(1, 1)):
    return {
        "captured_at": "2026-09-05T20:00:00+00:00",
        "match": {"flashscore_event_id": "flow-score-epoch", "home": "A", "away": "B", "minute": 88, "home_score": score[0], "away_score": score[1], "is_finished": False},
        "providers": {"flashscore": {"meta": {}}},
        "matchbook_exchange": {"available": True, "systems": {"money_flow": {
            "available": True, "liquid": True, "period": "FT", "line": sum(score) + 0.5,
            "market_id": "m1", "market_name": "Total", "market_status": "open", "volume": 2000.0,
            "fair_over": 0.62,
            "over": {"best_back": {"odds": 1.70, "available": 300.0}, "best_lay": {"odds": 1.72, "available": 250.0}},
            "under": {"best_back": {"odds": 2.40, "available": 200.0}, "best_lay": {"odds": 2.44, "available": 200.0}},
            "flow": {"window_ready_30s": True, "window_ready_60s": True, "volume_delta_30s": 500.0, "volume_delta_60s": 700.0, "fair_over_delta_pp_30s": 3.0, "fair_over_delta_pp_60s": 3.2},
        }}},
    }


def test_money_flow_score_change_blocks_without_goal_timeline(monkeypatch):
    multi_money_flow._FLOW_SCORE_STATE.clear()
    clock = [1000.0]
    monkeypatch.setattr(multi_money_flow, "_flow_now", lambda record: clock[0])
    first = _flow_record((1, 1))
    assert multi_money_flow.evaluate_money_flow(first)["eligible"] is True
    clock[0] += 15
    changed = _flow_record((2, 1))
    blocked = multi_money_flow.evaluate_money_flow(changed)
    assert blocked["eligible"] is False
    assert blocked["reason"] == "post_goal_exchange_reset"
    assert blocked["score_epoch_reset"] is True
    clock[0] += 181
    assert multi_money_flow.evaluate_money_flow(changed)["eligible"] is True


def test_matchbook_pagination_keeps_first_page_if_later_page_fails(monkeypatch):
    monkeypatch.setenv("MATCHBOOK_EVENTS_PER_PAGE", "20")
    monkeypatch.setenv("MATCHBOOK_MAX_PAGES", "3")
    def fake_page(page, per_page):
        if page == 1:
            return {"events": [{"id": f"e{i}"} for i in range(20)]}
        raise TimeoutError("slow second page")
    monkeypatch.setattr(matchbook_pagination, "_page_payload", fake_page)
    monkeypatch.setattr(matchbook_pagination, "decode_event", lambda row: {"event_id": row["id"], "home": "H", "away": "A", "start": None, "totals": {}})
    rows = matchbook_pagination.fetch_events_paginated()
    assert len(rows) == 20


def test_half_history_fetch_is_nonblocking(monkeypatch):
    gate = threading.Event()
    class SlowProvider:
        def half_prematch_context(self, home, away, limit=6):
            gate.wait(2.0)
            return {"home_recent": [], "away_recent": [], "h2h": []}
    prematch_goal_profile._HALF_CONTEXT_CACHE.clear()
    prematch_goal_profile._HALF_CONTEXT_FUTURES.clear()
    monkeypatch.setattr(prematch_goal_profile, "_HALF_PROVIDER", SlowProvider())
    record = {"match": {"flashscore_event_id": "async365", "home": "A", "away": "B", "minute": 20, "home_score": 0, "away_score": 0, "is_halftime": False}, "prematch_context": {}, "providers": {"flashscore": {"meta": {}}}}
    experts = {"goal_before_ht": {"state": "PASS", "probability": 0.75, "passed": True, "diagnostics": {}}}
    started = time.monotonic()
    profile = prematch_goal_profile.apply_half_goal_prior(record, experts)
    elapsed = time.monotonic() - started
    assert elapsed < 0.5
    assert profile["lazy_365_pending"] is True
    gate.set()
