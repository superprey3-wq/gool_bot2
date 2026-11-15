"""CI-only full evaluate_next_goal safety tests against exact PROGRUZ1 source.

External history/Flashscore adapters are stubbed only at import boundary.
Core scenario, selection, confidence and decision are exact source copies.
"""
import os
import sys
import types
import pytest


fake_fs=types.ModuleType("gool_adaptive_flashscore")
fake_fs.team_similarity=lambda a,b:1.0 if a==b else 0.0
sys.modules["gool_adaptive_flashscore"]=fake_fs
fake_early=types.ModuleType("gool_adaptive_early")
fake_early._history_rows=lambda *a,**k:[]
sys.modules["gool_adaptive_early"]=fake_early
fake_poisson=types.ModuleType("progruz1_poisson_shadow")
fake_poisson.estimate_next_goal=lambda **kw:{
    "status":"UNCALIBRATED_SHADOW",
    "estimated_goal_probability":0.60,
    "market_fair_over_probability":0.64}
sys.modules["progruz1_poisson_shadow"]=fake_poisson

from gool_flashscore_goal_brain import evaluate_next_goal


def setup_context(score=(0,0), minute=65,shots=(11,4),sot=(6,1)):
    return {
        "event_id":"E000001",
        "minute":minute,
        "score":score,"first_half_score":score,
        "flashscore_status_code":"13",
        "stats":{"shots":shots,"shots_on_target":sot,"xg":(1.1,.7)}}


def setup_quote(score=(0,0)):
    return {"root_id":"FS:E000001","kickoff":2000000000,
            "event_id":"match-next-goal","period":"Матч",
            "line":sum(score)+.5,
            "over":1.65,"under":2.10,"match_score":score}


def pressure():
    return {"sample_age":540,"recent_age":300,"recent_shots":4,
            "recent_sot":2,"stats_consistent":True}


@pytest.mark.parametrize("decision_mode",["SHADOW","OFF","JOINT"])
@pytest.mark.parametrize("scenario_mode",["OFF","SHADOW"])
def test_no_unverified_65m_two_sided_alert_in_any_mode(
        monkeypatch,scenario_mode,decision_mode):
    monkeypatch.setenv("GOOL_NEXT_GOAL_DECISION_MODE",decision_mode)
    monkeypatch.setenv("GOOL_NEXT_GOAL_SCENARIOS",scenario_mode)
    monkeypatch.setenv("GOOL_NEXT_GOAL_POISSON_SHADOW","0")
    audit={}
    pick,reason=evaluate_next_goal(
        [setup_quote()],setup_context(),pressure(),
        {"state":"READY","avg_total":3.2},decision_sink=audit)
    assert pick is None
    assert reason=="TWO_SIDED_RECENT_TEAM_STATS_UNVERIFIED"
    assert audit["joint"]["approved"] is False


def test_real_two_sided_65min_pulse_still_admitted(monkeypatch):
    monkeypatch.setenv("GOOL_NEXT_GOAL_DECISION_MODE","SHADOW")
    monkeypatch.setenv("GOOL_NEXT_GOAL_SCENARIOS","SHADOW")
    monkeypatch.setenv("GOOL_NEXT_GOAL_POISSON_SHADOW","0")
    p=pressure()
    p.update({
        "recent_10m_age":540,
        "recent_10m_shots_by_team":(4,3),
        "recent_10m_sot_by_team":(2,1),
        "recent_5m_age":300,
        "recent_5m_shots_by_team":(2,2),
        "recent_5m_sot_by_team":(1,1),
    })
    score=(1,1)
    pick,reason=evaluate_next_goal(
        [setup_quote(score)],
        setup_context(score=score,shots=(13,9),sot=(6,4)),
        p,{"state":"READY","avg_total":3.3})
    assert reason=="FS_NEXT_GOAL_CANDIDATE"
    assert pick["scenario"]["id"]=="TWO_SIDED"

@pytest.mark.parametrize("scenario",["QUALITY_SURGE","TRAILING_CHASE"])
def test_exceptional_verified_5m_goal_attack_not_lost_to_mild_low_form(
        monkeypatch,scenario):
    """A genuine powerful attack counts even when historic avg is 1.95."""
    monkeypatch.setenv("GOOL_NEXT_GOAL_DECISION_MODE","SHADOW")
    monkeypatch.setenv("GOOL_NEXT_GOAL_SCENARIOS","SHADOW")
    ctx={
        "event_id":"E0001","minute":60,
        "score":(1,1) if scenario=="QUALITY_SURGE" else (0,1),
        "first_half_score":(0,0),
        "flashscore_status_code":"13",
        "stats":{"shots":(12,5),"shots_on_target":(5,1),
                 "xg":(1.1,0.8)}}
    pressure={
        "sample_age":540,"recent_age":300,
        "recent_shots":5,"recent_sot":2,"stats_consistent":True,
        "recent_5m_age":300,
        "recent_5m_shots_by_team":(4,0),
        "recent_5m_sot_by_team":(2,0)}
    if scenario=="QUALITY_SURGE":
        ctx["recent_5m_xg_by_team"]=(0.46,0.02)
        ctx["recent_5m_xg_age"]=300
    score=ctx["score"]
    quote={"root_id":"FS:E0001","kickoff":2_000_000_000,
           "event_id":"live","period":"Матч",
           "line":sum(score)+.5,"over":1.65,"under":2.14,
           "match_score":score}
    trace={}
    pick,reason=evaluate_next_goal([quote],ctx,pressure,
        {"state":"READY","avg_total":1.95},decision_sink=trace)
    assert reason=="FS_NEXT_GOAL_CANDIDATE"
    assert pick is not None
    assert pick["scenario"]["id"]==scenario
    assert "VERIFIED_5M_PRESSURE_OVERRIDES_MILD_LOW_FORM" in (
        pick["scenario"]["notes"])


def test_mild_low_form_still_blocks_uncertain_and_unverified_live_attacks(
        monkeypatch):
    monkeypatch.setenv("GOOL_NEXT_GOAL_DECISION_MODE","SHADOW")
    monkeypatch.setenv("GOOL_NEXT_GOAL_SCENARIOS","SHADOW")
    ctx={"event_id":"E0001","minute":60,
         "score":(1,1),"first_half_score":(0,0),
         "flashscore_status_code":"13",
         "stats":{"shots":(12,8),"shots_on_target":(5,3),
                  "xg":(1.1,.8)}}
    base={"sample_age":540,"recent_age":300,
          "recent_shots":5,"recent_sot":2,"stats_consistent":True,
          "recent_10m_age":540,"recent_10m_shots_by_team":(4,3),
          "recent_10m_sot_by_team":(2,1)}
    quote={"root_id":"FS:E0001","kickoff":2_000_000_000,
           "event_id":"live","period":"Матч","line":2.5,
           "over":1.65,"under":2.14,"match_score":(1,1)}
    pick,reason=evaluate_next_goal(
        [quote],ctx,base,{"state":"READY","avg_total":1.95})
    assert pick is None
    assert reason=="FS_TEAM_GOAL_FORM_LOW"


def test_extremely_low_goal_form_stays_blocked_even_with_fresh_surge(monkeypatch):
    monkeypatch.setenv("GOOL_NEXT_GOAL_DECISION_MODE","SHADOW")
    ctx={"event_id":"E0001","minute":60,
         "score":(1,1),"first_half_score":(0,0),
         "flashscore_status_code":"13",
         "stats":{"shots":(12,5),"shots_on_target":(5,1),
                  "xg":(1.1,.8)},
         "recent_5m_xg_by_team":(0.46,0.02),
         "recent_5m_xg_age":300}
    pressure={"sample_age":540,"recent_age":300,
              "recent_shots":5,"recent_sot":2,
              "stats_consistent":True,
              "recent_5m_age":300,
              "recent_5m_shots_by_team":(4,0),
              "recent_5m_sot_by_team":(2,0)}
    quote={"root_id":"FS:E0001","kickoff":2_000_000_000,
           "event_id":"live","period":"Матч","line":2.5,
           "over":1.65,"under":2.14,"match_score":(1,1)}
    pick,reason=evaluate_next_goal(
        [quote],ctx,pressure,{"state":"READY","avg_total":1.60})
    assert pick is None
    assert reason=="FS_TEAM_GOAL_FORM_LOW"
