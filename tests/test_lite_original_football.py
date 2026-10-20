"""Contracts proving GOOL Lite delegates PREMATCH to actual Football V5
and LIVE to unchanged Brain V4 from the main branch."""
from __future__ import annotations

import time
from types import SimpleNamespace

from gool_bot2.football_v5_shadow import forecast_from_history
from gool_bot2.football_v5_production import production_v5_picks
from gool_bot2.lite_football_v5_adapter import normalized_full_market, original_v5_picks, history_identity_name
from gool_bot2.lite_football import LiteFootballWorker
from gool_bot2.lite_football_service import FootballHost


def history():
    h = [{"event_id":f"h{k}", "timestamp":1680000000+k*86400,
          "home":"Alpha FC", "away":f"Opponent H{k}",
          "home_score":2, "away_score":2} for k in range(10)]
    a = [{"event_id":f"a{k}", "timestamp":1680000000+k*86400,
          "home":f"Opponent A{k}", "away":"Beta FC",
          "home_score":2, "away_score":2} for k in range(10)]
    return {"home_recent":h, "away_recent":a, "home_at_home":h, "away_away":a, "h2h":[]}


def quote():
    return {
        "settings":{"bookmakers":[{"bookmaker":{"id":7,"name":"ExactTestBook"}}]},
        "odds":[
            {"bookmakerId":7,"bettingScope":"FULL_TIME","bettingType":"OVER_UNDER","odds":[
                {"selection":"OVER","value":2.8,"handicap":{"value":2.5}},
                {"selection":"UNDER","value":1.6,"handicap":{"value":2.5}},
                {"selection":"OVER","value":2.2,"handicap":{"value":3.5}},
                {"selection":"UNDER","value":1.7,"handicap":{"value":3.5}},
            ]},
            {"bookmakerId":7,"bettingScope":"FULL_TIME","bettingType":"BOTH_TEAMS_TO_SCORE","odds":[
                {"bothTeamsToScore":True,"value":1.95},
                {"bothTeamsToScore":False,"value":1.9},
            ]},
            {"bookmakerId":7,"bettingScope":"FULL_TIME","bettingType":"HOME_DRAW_AWAY","odds":[
                {"selection":"HOME","value":2.7},
                {"selection":"DRAW","value":2.8},
                {"selection":"AWAY","value":3.2},
            ]},
        ],
    }


def test_adapter_preserves_original_v5_results_and_market_id():
    ctx = history()
    kickoff = time.time()+3600
    forecast = forecast_from_history(home="Alpha FC",away="Beta FC",
                                     kickoff=kickoff,context=ctx)
    assert forecast["status"] == "READY"
    raw = quote()
    converted = normalized_full_market(raw)
    picks, _ = production_v5_picks(
        forecast,event_id="ABCDEFGH",home="Alpha FC",away="Beta FC",
        league="Test",kickoff_ts=kickoff,
        xbet=None,full_analysis=converted,
    )
    wrapped, info = original_v5_picks(
        history=ctx,raw_odds=raw,event_id="ABCDEFGH",home="Alpha FC",away="Beta FC",
        league="Test",kickoff=kickoff,
    )
    assert picks
    assert info["model"] == "FOOTBALL_V5"
    direct = {(p.market,p.selection,round(p.odds,3)) for p in picks}
    sent = {(p["market"],p["original_selection"],p["odd"]) for p in wrapped}
    assert direct == sent
    assert all(p["bookmaker"] == "ExactTestBook" for p in wrapped)


def test_adapter_never_pieces_together_unpaired_prices():
    raw = quote()
    broken = raw["odds"][0]
    broken["odds"] = [r for r in broken["odds"] if r["selection"] == "OVER"]
    converted = normalized_full_market(raw)
    assert not [r for r in converted["candidates"] if r["market_type"] == "OVER_UNDER"]


def test_real_v5_markets_have_valid_settlement(tmp_path):
    h = FootballHost(tmp_path)
    final = {"is_finished":True,"home_score":2,"away_score":1}
    for market,choice,outcome in [
        ("match_total","over 2.5","won"),
        ("match_total","under 2.5","lost"),
        ("match_total","over 3.5","lost"),
        ("btts","yes","won"),("btts","no","lost"),
        ("match_1x2","home","won"),("match_1x2","away","lost"),
        ("home_total","over 1.5","won"),("away_total","under 1.5","won"),
    ]:
        item={"phase":"PREMATCH","market":market,
              "original_selection":choice,"event_id":"ABCDEFGH"}
        assert h._settle_one(item,final,{}) == outcome


def test_original_live_v4_no_bet_is_not_promoted_by_lite(monkeypatch,tmp_path):
    """The old special-case .62 Lite confidence may not override original V4."""
    from gool_bot2 import lite_football
    monkeypatch.setenv("GOOL_LITE_SCORES365_VERIFY","0")
    monkeypatch.setenv("GOOL_LITE_FOTMOB_VERIFY","0")
    class FakeProvider:
        def live_matches(self):
            return [SimpleNamespace(provider_match_id="ABCDEFGH",home="Alpha FC",
                                    away="Beta FC",league="Test",minute=25,
                                    home_score=0,away_score=0,is_halftime=False)]
        def fetch_stats(self,_):
            return {"shots":(5,4),"shots_on_target":(2,1),"corners":(2,1)}
    monkeypatch.setattr(lite_football, "evaluate_live_goals", lambda record: [
        SimpleNamespace(
            market="GOAL_BEFORE_HT",decision="NO_BET",confidence=.68,
            probability=.9,data_source="provider_xg",
            reasons=("momentum_ready=1","recent_signals=3","sources=1",
                     "threshold=0.55","red_cards=0"),
        )
    ])
    worker=LiteFootballWorker(tmp_path,provider=FakeProvider())
    row=worker.scan_live(now=time.time())
    assert "ORIGINAL_LIVE_V4" in row["brain"]
    assert "ORIGINAL_BRAIN_V3" in row["brain"]
    assert row["decisions"][0]["decision"] == "NO_BET"
    assert row["filters"]["brain_bet"] == 0


def test_original_v5_accepts_only_history_confirmed_country_suffix_identity(tmp_path):
    ctx = history()
    assert history_identity_name("Alpha FC (Bhu)", ctx) == "Alpha FC"
    assert history_identity_name("Beta FC (Leb)", ctx) == "Beta FC"
    assert history_identity_name("Other (Bhu)", ctx) == "Other (Bhu)"
    assert history_identity_name("Alpha FC (U21)", ctx) == "Alpha FC (U21)"
    class FakeProvider:
        def fetch_match_history(self, event_id, home, away, limit):
            assert home == "Alpha FC (Bhu)" and away == "Beta FC (Leb)"
            return ctx
    work = LiteFootballWorker(tmp_path, provider=FakeProvider())
    fixture = SimpleNamespace(provider_match_id="ABCDEFGH", home="Alpha FC (Bhu)",
                              away="Beta FC (Leb)", league="test",
                              meta={"scheduled_start_ts":time.time()+3600})
    forecast, _ = work._forecast(fixture)
    assert forecast["status"] == "READY"
    assert forecast["home_games"] >= 6 and forecast["away_games"] >= 6
