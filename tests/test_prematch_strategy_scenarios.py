"""Prospective PREMATCH scenario research tests; never a betting backtest."""
from gool_bot2.prematch_strategy_scenarios import evaluate_prematch_scenarios
from gool_bot2.football_v5_production import production_v5_picks
from gool_bot2.football_v5_shadow import score_grid
from gool_bot2.full_report_export import _diagnostic_text


def by_id(result, code):
    return next(s for s in result["scenarios"] if s["id"] == code)


def hockey_features():
    return {
        "home_recent_n": 10, "away_recent_n": 10,
        "home_gf_avg": 4.2, "home_ga_avg": 3.7,
        "away_gf_avg": 3.2, "away_ga_avg": 4.0,
        "recent_total_avg": 8.3,
        "home_venue_gf_avg": 4.2, "away_venue_gf_avg": 3.2,
        "home_rest_days": 3, "away_rest_days": 1,
        "rest_advantage_days": 2, "away_back_to_back": True,
    }


def basketball_features():
    return {
        "home_recent_n": 10, "away_recent_n": 10,
        "home_gf_avg": 120, "home_ga_avg": 120,
        "away_gf_avg": 118, "away_ga_avg": 115,
        "home_venue_gf_avg": 130, "away_venue_gf_avg": 125,
        "recent_total_avg": 275, "venue_total_avg": 265,
    }


def test_football_named_scenarios_only_use_real_prematch_forecast():
    forecast = {"status": "READY", "home_lambda": 1.6, "away_lambda": 1.2, "quality": .9}
    q = {"match_totals": [{"line": 2.5, "over": 1.91, "under": 1.9}],
         "btts": {"yes": 1.89, "no": 1.9}}
    result = evaluate_prematch_scenarios("football", football_forecast=forecast, football_quote=q)
    assert result["status"] == "SHADOW_ONLY"
    assert by_id(result, "F01")["status"] == "NO_DATA"
    assert by_id(result, "F02")["status"] == "MATCH"
    assert by_id(result, "F02")["quoted_market_sides"] == 2
    assert by_id(result, "F02")["brain_qualified_market_sides"] == 0
    assert result["publication_enabled"] is False


def test_football_missing_history_wont_invent_goal_pick():
    result = evaluate_prematch_scenarios(
        "football", football_forecast={"status": "WAIT_HISTORY", "quality": 1.0},
        football_quote={"match_totals": [{"line": 2.5, "over": 1.8, "under": 2.0}]},
    )
    assert result["matched_count"] == 0
    assert all(s["status"] == "NO_DATA" for s in result["scenarios"])


def test_football_scenarios_connected_to_v5_diagnostics_without_affecting_picks():
    model = {"status": "READY", "home_lambda": 2.3, "away_lambda": 1.0,
             "quality": .9, "opponent_strength_coverage": 0,
             "probabilities": score_grid(2.3, 1.0)}
    quote = {
        "match_totals": [{"line": 1.5, "over": 1.6, "under": 2.4}],
        "home_totals": [{"line": .5, "over": 1.65, "under": 2.1}],
        "match_1x2": {"home": 2.25, "draw": 3.1, "away": 3.5},
        "btts": {"yes": 1.88, "no": 1.88},
    }
    picks, info = production_v5_picks(
        model, event_id="fixture", home="Alpha", away="Beta",
        league="Test", kickoff_ts=1791702000, xbet=quote, full_analysis=None,
    )
    assert picks
    assert info["prematch_scenarios"]["status"] == "SHADOW_ONLY"
    assert by_id(info["prematch_scenarios"], "F04")["status"] == "MATCH"
    assert all(s["publication_enabled"] is False for s in info["prematch_scenarios"]["scenarios"])


def test_hockey_attack_and_rest_are_detectable_but_goalie_and_pp_missing():
    rows = [
        {"market_family": "match_total", "scope": "FULL_MATCH",
         "line": 5.5, "over": 1.85, "under": 1.93},
        {"market_family": "handicap", "scope": "FULL_MATCH",
         "selection_side": "home", "line": -1.5, "odd": 1.95},
    ]
    result = evaluate_prematch_scenarios(
        "hockey", features=hockey_features(), league="KHL", market_lanes=rows,
    )
    assert by_id(result, "H01")["status"] == "MATCH"
    assert by_id(result, "H05")["status"] == "MATCH"
    assert by_id(result, "H04")["status"] == "NO_DATA"
    assert by_id(result, "H06")["status"] == "NO_DATA"
    assert by_id(result, "H01")["quoted_market_sides"] == 1


def test_missing_form_never_triggers_hockey_favorite():
    data = evaluate_prematch_scenarios(
        "hockey", features={"home_recent_n": 0, "away_recent_n": 0},
        league="NHL", market_lanes=[],
    )
    assert by_id(data, "H03")["status"] == "NO_DATA"
    assert data["matched_count"] == 0


def test_basketball_high_totals_and_segment_memory_respect_10_games():
    profile = basketball_features()
    memory = {
        "quality": .75,
        "segments": {
            "QUARTER_1": {"expected_home": 35, "expected_away": 33},
            "QUARTER_2": {"expected_home": 23, "expected_away": 24},
        },
    }
    rows = [
        {"market_family": "match_total", "scope": "FULL_MATCH",
         "line": 228.5, "over": 1.85, "under": 1.94},
        {"market_family": "match_total", "scope": "QUARTER_1",
         "line": 54.5, "over": 1.84, "under": 1.92},
    ]
    outcome = evaluate_prematch_scenarios(
        "basketball", features=profile, league="NBA",
        segment_memory=memory, market_lanes=rows,
    )
    assert by_id(outcome, "B01")["status"] == "MATCH"
    assert by_id(outcome, "B05")["status"] == "MATCH"
    assert by_id(outcome, "B05")["quoted_market_sides"] == 2
    assert by_id(outcome, "B03")["status"] in {"MATCH", "WATCH"}
    assert by_id(outcome, "B06")["status"] == "NO_DATA"
    assert outcome["publication_enabled"] is False


def test_day_report_labels_pattern_as_shadow_not_signal():
    result = evaluate_prematch_scenarios(
        "football",
        football_forecast={"status": "READY", "home_lambda": 1.6, "away_lambda": 1.2, "quality": .9},
    )
    message = _diagnostic_text({"sport": "football", "phase": "PREMATCH", "prematch_scenarios": result})
    assert "PREMATCH-сценарии SHADOW, НЕ СТАВКИ" in message
    assert "F02" in message
