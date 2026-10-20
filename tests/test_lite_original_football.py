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


def test_scenario_registries_separate_and_prematch_requires_real_v5_quote(tmp_path):
    from gool_bot2.lite_football_scenarios import prematch_scenarios
    ctx = history()
    forecast = forecast_from_history(
        home="Alpha FC", away="Beta FC", kickoff=time.time() + 3600, context=ctx,
    )
    assert forecast["status"] == "READY"
    offline = prematch_scenarios(forecast, football_quote={}, qualified_picks=[])
    assert [row["id"] for row in offline["scenarios"]] == [
        "F01", "F02", "F03", "F04", "F05",
    ]
    assert offline["qualified_pattern_count"] == 0
    approved, _ = original_v5_picks(
        history=ctx, raw_odds=quote(), event_id="ABCDEFGH",
        home="Alpha FC", away="Beta FC", league="Test",
        kickoff=time.time()+3600,
    )
    checked = prematch_scenarios(forecast, football_quote=quote(), qualified_picks=approved)
    assert checked["matched_count"] == offline["matched_count"]
    assert checked["qualified_pattern_count"] >= 1
    assert all(row.get("status") in {"MATCH", "WATCH", "NO_DATA"} for row in checked["scenarios"])


def test_independent_live_v3_builds_actual_five_minute_memory_from_flashscore():
    from gool_bot2.lite_football_scenarios import evaluate_live_scenarios, forget_live_history
    mid = "TEST_LIVE_SCENARIOS_1"
    forget_live_history(mid)
    start = time.time()
    previous = None
    for offset, minute in enumerate((20, 23, 26, 29)):
        record = {
            "match": {"flashscore_event_id":mid, "minute":minute,
                      "home_score":0, "away_score":0, "is_halftime":False},
            "providers":{"flashscore":{"stats":{
                "shots":(3+offset*3, 2+offset),
                "shots_on_target":(1+offset, 0+offset),
                "big_chances":(0 if offset < 2 else 1, 0),
                "corners":(1+offset,1),
            }, "meta":{}}},
            "live_momentum": {
                "minutes_in_epoch": minute-20,
                "shots_total_last_5m": 4.0,
                "sot_total_last_5m": 2.0,
            },
        }
        previous = evaluate_live_scenarios(record, mid, observed_at=start+offset*180)
    assert previous["brain"] == "ORIGINAL_BRAIN_V3"
    assert previous["market"] == "GOAL_BEFORE_HT"
    assert previous["momentum_ready"]
    assert previous["score_epoch_samples"] == 4
    assert len(previous["scenarios"]) == 5
    assert {x["id"] for x in previous["scenarios"]} == {"L01","L02","L03","L04","L05"}
    assert previous["data_quality"] >= .35
    forget_live_history(mid)


def test_live_v3_verified_bet_can_request_pricing_without_overriding_v4(tmp_path, monkeypatch):
    from gool_bot2 import lite_football
    monkeypatch.setenv("GOOL_LITE_SCORES365_VERIFY","0")
    monkeypatch.setenv("GOOL_LITE_FOTMOB_VERIFY","0")
    class FS:
        def live_matches(self):
            return [SimpleNamespace(provider_match_id="ABCDEFGH",home="Alpha FC",
                                    away="Beta FC",league="Test",minute=25,
                                    home_score=0,away_score=0,is_halftime=False)]
        def fetch_stats(self, mid):
            return {"shots":(8,4),"shots_on_target":(4,2),"corners":(3,2)}
    def verified_v3(record, mid, *, observed_at):
        return {
            "brain":"ORIGINAL_BRAIN_V3","status":"BET","decision":"BET",
            "strategy":"goal_before_ht","market":"GOAL_BEFORE_HT",
            "probability":.83,"bet_min":.78,"data_quality":.65,
            "blocks":[],"pressure_state":"HOME_SIEGE",
            "scenarios":[{"id":"L01","status":"BET"}],
        }
    monkeypatch.setattr(lite_football, "evaluate_live_scenarios", verified_v3)
    work = LiteFootballWorker(tmp_path,provider=FS())
    output = work.scan_live(now=time.time())
    v3 = [d for d in output["decisions"] if d.get("brain")=="ORIGINAL_BRAIN_V3"]
    assert len(v3)==1 and v3[0]["decision"]=="BET"
    assert v3[0]["confidence_required"]==.78
    assert v3[0]["publishable"] is False
    assert output["filters"]["v3_bet"]==1


def test_original_v4_strong_policy_can_price_only_with_quality_and_true_momentum(monkeypatch, tmp_path):
    """A healthy source policy is not held hostage by the unreachable V4 .78 cap."""
    from gool_bot2 import lite_football
    monkeypatch.setenv("GOOL_LITE_SCORES365_VERIFY", "0")
    monkeypatch.setenv("GOOL_LITE_FOTMOB_VERIFY", "0")
    monkeypatch.setenv("GOOL_LITE_ORIGINAL_V4_POLICY", "1")
    class FS:
        def live_matches(self):
            return [SimpleNamespace(provider_match_id="ABCDEFGH", home="Alpha FC",
                                    away="Beta FC", league="Test", minute=28,
                                    home_score=0, away_score=0, is_halftime=False)]
        def fetch_stats(self, mid):
            return {"shots":(13,9), "shots_on_target":(5,3), "big_chances":(2,1)}
    def v4_no_bet(record):
        return [SimpleNamespace(
            market="GOAL_BEFORE_HT", decision="NO_BET", confidence=.73,
            probability=.94, data_source="attack_proxy",
            reasons=("momentum_ready=1", "recent_signals=3", "sources=1",
                     "threshold=0.67", "red_cards=0"),
        )]
    def v3_no_bet(record, mid, *, observed_at):
        return {"brain":"ORIGINAL_BRAIN_V3", "decision":"NO_BET", "status":"WATCH",
                "strategy":"goal_before_ht", "probability":.65,
                "data_quality":.80, "blocks":[], "scenarios":[]}
    monkeypatch.setattr(lite_football, "evaluate_live_goals", v4_no_bet)
    monkeypatch.setattr(lite_football, "evaluate_live_scenarios", v3_no_bet)
    result=LiteFootballWorker(tmp_path, provider=FS()).scan_live(now=time.time())
    assert result["errors"] == 0
    candidates=[d for d in result["decisions"]
                if d["brain"] == "ORIGINAL_V4_POLICY_STRONG"]
    assert len(candidates) == 1
    assert candidates[0]["decision"] == "BET"
    assert candidates[0]["confidence_required"] == .62
    assert candidates[0]["priced"] is False
    assert result["filters"]["v4_policy_strong"] == 1
    # This did NOT rewrite or falsely label the original V4 NO_BET.
    strict=[d for d in result["decisions"] if d["brain"]=="ORIGINAL_LIVE_V4"]
    assert strict[0]["decision"]=="NO_BET"

    def low_quality(record, mid, *, observed_at):
        data=v3_no_bet(record, mid, observed_at=observed_at)
        data["data_quality"] = .40
        return data
    monkeypatch.setattr(lite_football, "evaluate_live_scenarios", low_quality)
    result=LiteFootballWorker(tmp_path/"weak", provider=FS()).scan_live(now=time.time())
    assert result["filters"]["v4_policy_strong"] == 0
    assert not [d for d in result["decisions"]
                if d["brain"] == "ORIGINAL_V4_POLICY_STRONG"]


def test_v4_policy_never_promotes_red_card_live_scenario(monkeypatch,tmp_path):
    from gool_bot2 import lite_football
    monkeypatch.setenv("GOOL_LITE_SCORES365_VERIFY", "0")
    monkeypatch.setenv("GOOL_LITE_FOTMOB_VERIFY", "0")
    class Provider:
        def live_matches(self):
            return [SimpleNamespace(provider_match_id="ABCDEFGH",home="Alpha",
                                    away="Beta",league="Test",minute=25,
                                    home_score=0,away_score=0,is_halftime=False)]
        def fetch_stats(self,mid):
            return {"shots":(10,8),"shots_on_target":(4,3),"big_chances":(1,1)}
    monkeypatch.setattr(lite_football,"evaluate_live_scenarios",
        lambda record,mid,*,observed_at:{
            "decision":"NO_BET","status":"WATCH","strategy":"goal_before_ht",
            "probability":.6,"data_quality":.90,"blocks":[],"scenarios":[]})
    monkeypatch.setattr(lite_football,"evaluate_live_goals",
        lambda record:[SimpleNamespace(
            market="GOAL_BEFORE_HT",decision="NO_BET",confidence=.73,
            probability=.95, data_source="attack_proxy",
            reasons=("momentum_ready=1","recent_signals=3",
                     "red_cards=1","threshold=.62"))])
    row=LiteFootballWorker(tmp_path,provider=Provider()).scan_live(now=time.time())
    assert row["errors"] == 0
    assert row["filters"]["v4_policy_strong"] == 0
    assert not any(d.get("brain")=="ORIGINAL_V4_POLICY_STRONG"
                   for d in row["decisions"])
