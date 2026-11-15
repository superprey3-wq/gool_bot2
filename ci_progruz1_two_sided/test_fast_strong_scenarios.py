"""Real verified short-lived signals -- independent 5 minute high-quality paths.

These do not create simulated odds or historical fake profits.
"""
import sqlite3
from progruz1_scenarios import classify_scenario
from progruz1_scenario_evidence import ScenarioEvidence
from gool_flashscore_goal_brain import evaluate_next_goal


def cls(*,minute=60,score=(1,1),shots=(12,5),sot=(5,1),
        recent_shots=5,recent_sot=2,**kwargs):
    return classify_scenario(
        minute=minute,score=score,second_half=True,
        shots=shots,sot=sot,recent_shots=recent_shots,
        recent_sot=recent_sot,odd=1.72,xg_available=True,
        avg_total=3.1,**kwargs)


def test_short_verified_xg_surge_is_approved_without_10m_history():
    args=dict(
        recent_5m_shots_by_team=(4,0),
        recent_5m_sot_by_team=(2,0),recent_5m_age=300,
        recent_5m_xg_by_team=(0.46,0.02),recent_5m_xg_age=300)
    verified=cls(**args)
    assert verified["id"]=="QUALITY_SURGE"
    assert "QUALITY_SURGE_CONFIRMED_XG_5M" in verified["notes"]
    assert cls(**{k:v for k,v in args.items()
                  if not k.startswith("recent_5m_xg")})["id"]=="NO_CLEAR_SCENARIO"
    assert cls(**{**args,"recent_5m_xg_age":999})["id"]=="NO_CLEAR_SCENARIO"
    assert cls(**{**args,"recent_5m_xg_by_team":(-.1,.1)})["id"]=="NO_CLEAR_SCENARIO"


def test_short_verified_trailing_chase_approved_only_with_strong_pulse():
    args=dict(score=(0,1),minute=62,
              recent_5m_shots_by_team=(4,0),
              recent_5m_sot_by_team=(2,0),recent_5m_age=300)
    assert cls(**args)["id"]=="TRAILING_CHASE"
    assert cls(**{**args,"recent_5m_sot_by_team":(1,0)})["id"]=="NO_CLEAR_SCENARIO"
    assert cls(**{**args,"recent_5m_age":700})["id"]=="NO_CLEAR_SCENARIO"
    assert cls(**{**args,"red_cards":(1,0)})["id"]=="NO_CLEAR_SCENARIO"


def test_short_xg_delta_comes_only_from_distinct_observed_samples():
    db=sqlite3.connect(":memory:")
    evidence=ScenarioEvidence(db)
    kick=2000000000
    now=kick+60*60
    first=evidence.observe(root="FS:abc",kickoff=kick,
                          now=now-300,phase="2-й тайм",
                          xg=(.6,.4),pressure={})
    assert first["recent_5m_xg_by_team"] is None
    verified=evidence.observe(root="FS:abc",kickoff=kick,
                          now=now,phase="2-й тайм",
                          xg=(1.05,.42),pressure={})
    assert verified["recent_5m_xg_age"]==300
    assert round(verified["recent_5m_xg_by_team"][0],2)==0.45
    assert round(verified["recent_5m_xg_by_team"][1],2)==0.02
    corrected=evidence.observe(root="FS:abc",kickoff=kick,
                          now=now+300,phase="2-й тайм",
                          xg=(.7,.42),pressure={})
    assert corrected["recent_5m_xg_by_team"] is None


def test_next_goal_engine_accepts_real_short_xg_surge(monkeypatch):
    monkeypatch.setenv("GOOL_NEXT_GOAL_DECISION_MODE","SHADOW")
    monkeypatch.setenv("GOOL_NEXT_GOAL_SCENARIOS","SHADOW")
    monkeypatch.setenv("GOOL_NEXT_GOAL_POISSON_SHADOW","0")
    ctx={
        "event_id":"E000001","minute":60,"score":(1,1),
        "first_half_score":(1,1),"flashscore_status_code":"13",
        "stats":{"shots":(12,5),"shots_on_target":(5,1),
                 "xg":(1.6,.9)},
        "recent_5m_xg_by_team":(.46,.02),"recent_5m_xg_age":300}
    pressure={"sample_age":600,"recent_age":300,
              "recent_shots":5,"recent_sot":2,"stats_consistent":True,
              "recent_5m_shots_by_team":(4,0),
              "recent_5m_sot_by_team":(2,0),"recent_5m_age":300}
    quote={"root_id":"FS:E000001","kickoff":2000000000,
           "event_id":"price1","period":"Матч",
           "line":2.5,"over":1.72,"under":2.05,"match_score":(1,1)}
    history={"state":"READY","avg_total":3.2}
    pick,reason=evaluate_next_goal([quote],ctx,pressure,history)
    assert reason=="FS_NEXT_GOAL_CANDIDATE"
    assert pick["scenario"]["id"]=="QUALITY_SURGE"
    assert "xG за ~5м" in pick["pressure_detail"]


def test_legacy_two_sided_weak_evidence_remains_blocked():
    weak=cls(
        minute=65,score=(1,1),shots=(11,6),sot=(5,2),
        recent_10m_shots_by_team=(4,1),recent_10m_sot_by_team=(2,0),
        recent_10m_age=540)
    assert weak["id"]=="STATE_RISK"
    assert any(r.startswith("TWO_SIDED_") for r in weak["risk_flags"])
