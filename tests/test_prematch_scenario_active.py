"""Scenario-active PREMATCH requires real prices, independent Brain checks and rollback.

Synthetic examples test guard plumbing only; no claim of historical ROI.
"""
import pytest

from gool_bot2.prematch_scenario_active import (
    matching_scenario_ids, recover_multisport_prematch,
    recover_football_v6_prematch, recover_football_first_half_goal,
)
from gool_bot2.prematch_strategy_scenarios import evaluate_prematch_scenarios
from gool_bot2.v4_prematch_engine import PrematchPick
from gool_bot2.v4_prematch_settlement import settle_prematch_pick
from gool_bot2.full_report_export import _diagnostic_text


def _hockey():
    return {
        "home_recent_n": 10, "away_recent_n": 10,
        "home_gf_avg": 4.2, "home_ga_avg": 3.5,
        "away_gf_avg": 3.8, "away_ga_avg": 3.9,
        "recent_total_avg": 8.5,
        "home_venue_gf_avg": 4.3, "away_venue_gf_avg": 3.9,
        "home_rest_days": 3, "away_rest_days": 2,
    }


def _basketball():
    return {
        "home_recent_n": 10, "away_recent_n": 10,
        "home_gf_avg": 122, "home_ga_avg": 120,
        "away_gf_avg": 125, "away_ga_avg": 122,
        "home_venue_gf_avg": 125, "away_venue_gf_avg": 130,
        "recent_total_avg": 270, "venue_total_avg": 255,
        "home_rest_days": 2, "away_rest_days": 2,
    }


def test_hockey_scenario_fallback_uses_bookmaker_ev_and_records_scenario(monkeypatch):
    import gool_bot2.prematch_scenario_active as module
    lane = {"market_family": "match_total", "scope": "FULL_MATCH",
            "line": 2.5, "over": 1.90, "under": 1.92, "probability": .53}
    snapshot = evaluate_prematch_scenarios("hockey", features=_hockey(), league="KHL", market_lanes=[lane])
    assert matching_scenario_ids(snapshot, sport="hockey", family="match_total", direction="over") == ["H01"]
    def priced(*args, **kwargs):
        return {
            "status": "PASS_SHADOW", "direction": "over",
            "best_expected_value_units_uncalibrated": .30,
            "lambda_scoped": 4.5,
            "alternatives": [
                {"direction": "over", "odd": 1.90,
                 "conditional_win_probability_uncalibrated": .79,
                 "push_probability": 0.0}
            ],
        }
    monkeypatch.setattr(module, "evaluate_hockey_prematch_ev_shadow", priced, raising=False)
    # The callable is imported locally: patch original source instead.
    import gool_bot2.hockey_prematch_ev_shadow as evmod
    monkeypatch.setattr(evmod, "evaluate_hockey_prematch_ev_shadow", priced)
    recovered, diag = recover_multisport_prematch(
        sport="hockey", scenario_snapshot=snapshot, lanes=[lane],
        features=_hockey(), league="KHL",
    )
    assert diag["matching"] == 1
    assert len(recovered) == 1
    assert recovered[0][1]["brain_mode"] == "hockey_prematch_scenario_active"
    assert recovered[0][1]["scenario_activation"]["scenario_ids"] == ["H01"]
    assert recovered[0][1]["model_probability"] < .79


def test_hockey_scenario_never_bypasses_handicap_quarantine():
    features = _hockey()
    lane = {"market_family": "handicap", "scope": "FULL_MATCH",
            "line": -1.5, "odd": 1.90, "selection_side": "home"}
    snap = evaluate_prematch_scenarios("hockey", features=features, league="KHL")
    picks, _ = recover_multisport_prematch(
        sport="hockey", scenario_snapshot=snap, lanes=[lane],
        features=features, league="KHL",
    )
    assert picks == []


def test_basketball_scenario_activates_existing_v2_price_only(monkeypatch):
    import gool_bot2.basketball_brain_v2 as v2
    lane = {"market_family": "match_total", "scope": "FULL_MATCH",
            "line": 235.5, "over": 1.88, "under": 1.92, "probability": .5}
    snapshot = evaluate_prematch_scenarios(
        "basketball", features=_basketball(), league="NBA", market_lanes=[lane]
    )
    assert matching_scenario_ids(snapshot, sport="basketball", family="match_total", direction="over") == ["B01"]
    monkeypatch.setattr(v2, "prematch_signal", lambda *a, **kw: {
        "brain_mode": "basketball_prematch_v2", "direction": "over",
        "line": 235.5, "odd": 1.88, "model_probability": .73,
        "market_probability": .50, "mu_total": 246.0,
        "data_quality": .9, "strength": 77.0,
    })
    out, diag = recover_multisport_prematch(
        sport="basketball", scenario_snapshot=snapshot, lanes=[lane],
        features=_basketball(), league="NBA",
    )
    assert diag["qualified"] == 1
    assert len(out) == 1
    assert out[0][1]["scenario_activation"]["scenario_ids"] == ["B01"]
    assert out[0][1]["value_after_vig_proxy"] > .035
    assert out[0][1]["model_probability"] < .73


def test_basketball_requires_price_and_v2_evidence(monkeypatch):
    import gool_bot2.basketball_brain_v2 as v2
    lane = {"market_family": "match_total", "scope": "FULL_MATCH",
            "line": 235.5, "over": 1.55, "under": 1.91, "probability": .5}
    snap = evaluate_prematch_scenarios("basketball", features=_basketball(), league="NBA", market_lanes=[lane])
    monkeypatch.setattr(v2, "prematch_signal", lambda *a, **kw: {
        "direction": "over", "line": 235.5, "odd": 1.55,
        "model_probability": .85, "market_probability": .50, "mu_total": 245.0,
    })
    out, _ = recover_multisport_prematch(
        sport="basketball", scenario_snapshot=snap, lanes=[lane],
        features=_basketball(), league="NBA",
    )
    assert out == []


def test_scenario_recovery_can_be_disabled_instantly(monkeypatch):
    monkeypatch.setenv("GOOL_PREMATCH_SCENARIO_ACTIVE", "0")
    snap = evaluate_prematch_scenarios("basketball", features=_basketball(), league="NBA")
    out, _ = recover_multisport_prematch(
        sport="basketball", scenario_snapshot=snap, lanes=[],
        features=_basketball(), league="NBA",
    )
    assert out == []


def test_football_v6_near_miss_only_when_matched_and_real_price():
    snap = evaluate_prematch_scenarios(
        "football",
        football_forecast={"status": "READY", "home_lambda": 1.65,
                           "away_lambda": 1.15, "quality": .9},
    )
    p = PrematchPick("e1", "Home", "Away", "btts", "yes", 1.90, .615, .54, .9, "Test", 1791702000)
    res, meta = recover_football_v6_prematch(
        snap, tournament_candidates=[p],
        shadow={"quote_source": "1xbet", "data_quality": .9},
    )
    assert res == [p] and meta["scenario_ids"] == ["F02"]
    broken, _ = recover_football_v6_prematch(
        snap, tournament_candidates=[p],
        shadow={"quote_source": "none", "data_quality": .9},
    )
    assert broken == []


def test_first_half_uses_real_two_way_1xbet_market_and_historical_sample():
    snap = evaluate_prematch_scenarios(
        "football",
        football_forecast={"status": "READY", "home_lambda": 1.4,
                           "away_lambda": 1.25, "quality": .9,
                           "first_half": {"home_lambda": .9, "away_lambda": .8}},
    )
    quote = {"first_half_totals": [{"line": .5, "over": 1.90, "under": 1.90}]}
    profile = {"first_half": {
        "pair_sample": 10, "home_expected_goals": .9, "away_expected_goals": .8,
    }}
    kw = dict(pregame_profile=profile, quote=quote, football_quality=.9,
              event_id="e1", home="A", away="B", league="Test",
              kickoff_ts=1791702000)
    selections, meta = recover_football_first_half_goal(snap, **kw)
    assert len(selections) == 1
    assert selections[0].market == "first_half_total"
    assert selections[0].selection == "over 0.5"
    assert meta["scenario_ids"] == ["F01"]
    assert settle_prematch_pick(
        {"market": "first_half_total", "selection": "over 0.5"},
        2, 1, half_time_score=(1, 0),
    ) == "won"
    assert recover_football_first_half_goal(
        snap, **{**kw, "pregame_profile": {"first_half": {**profile["first_half"], "pair_sample": 2}}}
    )[0] == []


def test_dayreport_identifies_active_scenario_not_just_shadow():
    result = _diagnostic_text({
        "sport": "hockey", "phase": "PREMATCH",
        "scenario_activation": {"scenario_ids": ["H01"]},
    })
    assert "АКТИВНЫЙ PREMATCH СЦЕНАРИЙ" in result
