"""Offline checks of original GOOL football-only branch (no trained ML)."""
from __future__ import annotations

import json
import os
import time
from pathlib import Path
from types import SimpleNamespace

import football_original_no_ml as app


class FakeCollector:
    def __init__(self, record=None):
        self.flashscore = SimpleNamespace(scheduled_matches_for_day=lambda day: [])
        self.record = record or {
            "match": {"flashscore_event_id":"ABCDEFGH","minute":18,
                      "home":"Alpha FC","away":"Beta FC",
                      "home_score":0,"away_score":0,"is_halftime":False},
            "providers":{"flashscore":{"stats":{
                "shots":(5,4),"shots_on_target":(2,1),"corners":(2,2)}}},
            "prefilter":{"score":50.0,"candidate":True},
        }
        self._append = lambda record, now: None
        self.stopped = False

    def collect_once(self):
        self._append(self.record, None)
        return {"live":1,"entry_window":1,"detail":1,
                "entry_window_coverage_pct":100.0,"errors":0}

    def stop(self):
        self.stopped = True


class FakeGateway:
    def __init__(self):
        self.last_reject_reason=None
    def _get_collector(self):
        return self
    def _fetch_index(self):
        return ("legacy",[])
    def price(self,decision,index,now):
        self.last_reject_reason="MATCH_NOT_FOUND"
        return None


def test_original_production_engines_are_callable_without_model_files():
    from gool_bot2.brain_v3_memory import build_brain_v3_memory
    from gool_bot2.brain_v3_decision import evaluate_brain_v3
    from gool_bot2.live_goal_brain_v4 import evaluate_live_goals
    from gool_bot2.football_v5_production import production_v5_picks
    from gool_bot2.storage_live_collector import StorageLiveSnapshotCollector
    assert all((build_brain_v3_memory,evaluate_brain_v3,evaluate_live_goals,
                production_v5_picks,StorageLiveSnapshotCollector))
    app.smoke()


def test_no_send_in_shadow_and_original_collector_is_used(tmp_path, monkeypatch):
    monkeypatch.setenv("GOOL_ORIGINAL_FOOTBALL_MODE","shadow")
    f=FakeCollector()
    service=app.FootballOriginalService(tmp_path,collector=f,gateway=FakeGateway())
    before=list(service.journal)
    data=service.scan()
    assert data["detail"]==1
    assert service.audit["stats_scans"]==1
    assert service.audit["brain"]["evaluated"]==1
    assert service.journal==before
    assert "ОРИГИНАЛЬНЫЙ ФУТБОЛ" in service.status()


def test_no_ml_flags_and_distinct_current_launch_environment(monkeypatch):
    assert os.environ["GOOL_HOCKEY_ENABLED"]=="0"
    assert os.environ["GOOL_BASKETBALL_ENABLED"]=="0"
    assert os.environ["GOOL_BROWSER_ENABLE"]=="0"
    assert not app.active()


def test_gateway_fail_closed_when_index_cannot_match(tmp_path):
    from gool_bot2.football_original_price_gateway import OriginalFootballPriceGateway
    class Book:
        def _map(self,fixture,index):
            return None
    price=OriginalFootballPriceGateway(tmp_path,collector=Book())
    row={"event_id":"ABCDEFGH","home":"Alpha FC","away":"Beta FC",
         "score":[0,0],"minute":24,"market":"GOAL_BEFORE_HT",
         "observed_at":time.time(),"probability":.85,
         "confidence":.90,"confidence_required":.78,"decision":"BET"}
    assert price.price(row,[],now=time.time()) is None
    assert price.last_reject_reason=="MATCH_NOT_FOUND"


def test_original_collector_processes_only_football_without_telegram_in_smoke(tmp_path, monkeypatch):
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN",raising=False)
    monkeypatch.delenv("TELEGRAM_CHAT_ID",raising=False)
    f=FakeCollector()
    service=app.FootballOriginalService(tmp_path,collector=f,gateway=FakeGateway())
    assert service.scan_prematch()==0
    assert not app.send_text("test")
    assert service.audit.get("prematch_candidates",0)==0


def test_original_1xbet_live_price_from_verified_real_time_pair(tmp_path):
    from gool_bot2.football_original_price_gateway import OriginalFootballPriceGateway

    class OriginalBook:
        def _map(self, fixture, index):
            return "123"
        def _game(self, eid):
            assert eid == "123"
            # The old 1xBet parser correctly considers missing S1/S2 as 0:0.
            return {"SC": {"FS": {}}}
        def _markets_for_event(self, eid, minute, total):
            assert minute == 24 and total == 0
            return {"first_half_total": [
                {"line": 0.5, "over": 1.85, "under": 1.95},
            ]}
    gateway=OriginalFootballPriceGateway(tmp_path,collector=OriginalBook())
    decision={
        "event_id":"ABCDEFGH","home":"Alpha FC","away":"Beta FC",
        "score":[0,0],"minute":24,"market":"GOAL_BEFORE_HT",
        "observed_at":time.time(),"probability":.88,
        "confidence":.88,"confidence_required":.70,
        "decision":"BET","brain":"ORIGINAL_BRAIN_V3",
    }
    rows=[{"event_id":"123","home":"Alpha FC","away":"Beta FC"}]
    priced=gateway.price(decision,rows,now=time.time())
    assert priced is not None
    assert priced["odd"]==1.85
    assert priced["line"]==0.5
    assert priced["bookmaker"]=="1xBet LIVE"
