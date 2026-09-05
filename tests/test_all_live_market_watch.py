from __future__ import annotations

from datetime import datetime, timezone

from gool_bot2.matchbook_exchange import matchbook_context
from gool_bot2.multi_autonomous_steam import build_autonomous_steam_candidates
from gool_bot2.multi_concept import enforce_entry_cutoff
from gool_bot2.multi_money_flow import evaluate_money_flow
from gool_bot2.multi_router import MarketCandidate, RouterDecision
from gool_bot2.xbet_market_worker import DemandDrivenXBetMarketCollector


class _LiveMatch:
    def __init__(self, match_id: str, minute: int, *, finished: bool = False, halftime: bool = False):
        self.provider_match_id = match_id
        self.minute = minute
        self.is_finished = finished
        self.is_halftime = halftime
        self.league = "Any League"


def test_xbet_market_watch_has_no_minute_or_league_cap(monkeypatch):
    matches = [
        _LiveMatch("m1", 1),
        _LiveMatch("m2", 44),
        _LiveMatch("m3", 45, halftime=True),
        _LiveMatch("m4", 76),
        _LiveMatch("m5", 89),
        _LiveMatch("m6", 90),
        _LiveMatch("m7", 95),
        _LiveMatch("done", 90, finished=True),
    ]
    monkeypatch.setattr("gool_bot2.xbet_market_worker.load_active_demands", lambda: {"m5": {}})
    collector = object.__new__(DemandDrivenXBetMarketCollector)
    selected, stats = collector._select_matches(matches)
    assert [row.provider_match_id for row in selected] == ["m5", "m1", "m2", "m3", "m4", "m6", "m7"]
    assert stats["live_watch"] == 7
    assert stats["demanded"] == 1


def _steam_record(minute: int = 89):
    return {
        "match": {
            "minute": minute,
            "home_score": 1,
            "away_score": 1,
            "is_finished": False,
            "is_halftime": False,
        }
    }


def _steam_market():
    return {
        "captured_at": datetime.now(timezone.utc).isoformat(),
        "score_home": 1,
        "score_away": 1,
        "score_verified": True,
        "markets": {
            "match_total": [{"line": 2.5, "over": 1.70, "under": 2.20}],
        },
        "pressure": {
            "match_total:2.5": {
                "prob_delta_pp": 13.0,
                "one_way_moves": 4,
                "old_odd": 2.05,
            }
        },
    }


def test_autonomous_steam_can_fire_at_89_without_football_quality_gate(monkeypatch):
    monkeypatch.setenv("XBET_AUTONOMOUS_STEAM_MIN_RELATED_MARKETS", "1")
    rows = build_autonomous_steam_candidates(_steam_record(89), _steam_market(), data_quality=0.0)
    assert rows
    assert rows[0].source == "1xbet:autonomous_steam"
    assert rows[0].odd == 1.70


def _candidate(source: str) -> MarketCandidate:
    return MarketCandidate(
        key="match_total:2.5",
        family="match_total",
        label="ТБ 2.5",
        odd=1.70,
        model_probability=0.80,
        strategy="another_goal",
        source=source,
        rating=80.0,
    )


def test_ordinary_cutoff_does_not_block_autonomous_steam_after_75():
    steam = RouterDecision("BET", 89, (1, 1), _candidate("1xbet:autonomous_steam"), [], [], "steam")
    assert enforce_entry_cutoff(steam).status == "BET"

    ordinary = RouterDecision("BET", 89, (1, 1), _candidate("gool"), [], [], "ordinary")
    blocked = enforce_entry_cutoff(ordinary)
    assert blocked.status == "WAIT"
    assert blocked.winner is None


def _matchbook_state():
    flow = {
        "window_ready_30s": True,
        "window_ready_60s": True,
        "volume_delta_30s": 400.0,
        "volume_delta_60s": 550.0,
        "fair_over_delta_pp_30s": 2.8,
        "fair_over_delta_pp_60s": 3.0,
    }
    total = {
        "id": "tot25",
        "name": "Total Goals 2.5",
        "status": "open",
        "in_running": True,
        "volume": 2000.0,
        "period": "FT",
        "line": 2.5,
        "fair_over": 0.62,
        "over": {
            "best_back": {"odds": 1.70, "available": 300.0},
            "best_lay": {"odds": 1.72, "available": 250.0},
        },
        "under": {
            "best_back": {"odds": 2.40, "available": 200.0},
            "best_lay": {"odds": 2.44, "available": 200.0},
        },
        "flow": flow,
    }
    return {
        "captured_at": datetime.now(timezone.utc).isoformat(),
        "events": [{
            "event_id": "mb1",
            "name": "Arsenal vs Chelsea",
            "home": "Arsenal",
            "away": "Chelsea",
            "status": "open",
            "in_running": True,
            "allow_live": True,
            "volume": 5000.0,
            "totals": {"FT:2.5": total},
        }],
    }


def test_money_flow_can_fire_at_89_and_90_plus():
    for minute in (89, 90, 95):
        record = {
            "match": {
                "flashscore_event_id": "fs1",
                "home": "Arsenal",
                "away": "Chelsea",
                "minute": minute,
                "home_score": 1,
                "away_score": 1,
                "is_finished": False,
                "is_halftime": False,
            },
            "providers": {"flashscore": {"meta": {"goal_timeline": []}}},
        }
        record["matchbook_exchange"] = matchbook_context(record, _matchbook_state())
        context = record["matchbook_exchange"]["systems"]["money_flow"]
        assert context["available"] is True
        assert context["period"] == "FT"
        info = evaluate_money_flow(record)
        assert info["eligible"] is True
        assert info["odd"] == 1.70


def test_runtime_has_no_duplicate_confidence_brain():
    text = __import__("pathlib").Path("src/gool_bot2/multi_runtime.py").read_text()
    assert "enforce_confidence_gate" not in text
    assert "_enforce_min_rating" not in text
