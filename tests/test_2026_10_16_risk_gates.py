"""Regression protections for the GOOL daily audit dated 2026-10-16.

Synthetic inputs recreate the diagnostic shape of the reported bets. These
are NOT a historical backtest and do not assert profitability.
"""
from gool_bot2.hockey_brain_v2 import prematch_signal as hockey_prematch
from gool_bot2.basketball_brain_v2 import live_signal as basketball_live
from gool_bot2.basketball_v5_consensus import basketball_v5_prematch_signal
from gool_bot2.full_report_export import _runtime_live_audit_html


def _hockey_features():
    return {
        "home_recent_n": 10, "away_recent_n": 10,
        "home_gf_avg": 3.7, "home_ga_avg": 1.9,
        "away_gf_avg": 2.1, "away_ga_avg": 3.5,
        "home_venue_gf_avg": 3.9, "away_venue_gf_avg": 1.9,
        "home_rest_days": 2, "away_rest_days": 1,
    }


def test_uncalibrated_hockey_handicaps_are_quarantined_by_default(monkeypatch):
    monkeypatch.delenv("GOOL_HOCKEY_PREMATCH_ENABLE_HANDICAPS", raising=False)
    lane = {"scope": "FULL_MATCH", "market_family": "handicap",
            "selection_side": "away", "line": 2.5, "odd": 1.85,
            "probability": 0.50, "selection": "Ф2 +2.5"}
    assert hockey_prematch(lane, _hockey_features(), "KHL") is None


def test_extreme_hockey_team_under_is_not_extrapolated_from_capped_lambda(monkeypatch):
    monkeypatch.delenv("GOOL_HOCKEY_PREMATCH_MAX_TEAM_TOTAL_LINE", raising=False)
    lane = {"scope": "FULL_MATCH", "market_family": "away_total",
            "line": 9.0, "over": 1.88, "under": 1.88,
            "probability": 0.50}
    assert hockey_prematch(lane, _hockey_features(), "KHL") is None
    # The gate is configurable for controlled research, not a removed market.
    monkeypatch.setenv("GOOL_HOCKEY_PREMATCH_MAX_TEAM_TOTAL_LINE", "10.0")
    signal = hockey_prematch(lane, _hockey_features(), "KHL")
    assert signal is not None and signal["direction"] == "under"


def test_basketball_rejects_stale_delta_spike_against_real_quarter_score(monkeypatch):
    monkeypatch.delenv("GOOL_BASKETBALL_LIVE_MAX_DELTA_PRIOR_MULTIPLIER", raising=False)
    brain = {
        "brain_state": "PASS", "scope": "QUARTER_3", "history_points": 4,
        "recent_window_seconds": 90.0, "recent_score_rate": 13.459,
        "recent_possessions_per_min": 3.32, "recent_possessions": 5.0,
        "recent_activity_available": True, "segment_score_verified": True,
        "break_transition": False, "league": "COLOMBIA: LBP - Clausura",
        "score": [55, 46], "current_segment_score": [5, 6],
        "live_game_stats": {"current_segment_available": True,
                            "stats_mode": "cumulative_through_current_segment",
                            "segment_stats": {}, "segment_attempts": {}},
    }
    lane = {"scope": "QUARTER_3", "market_family": "match_total",
            "clock_seconds": 177, "line": 42.5, "score": [5, 6],
            "match_score": [55, 46], "probability": 0.50,
            "over": 1.84, "under": 1.84,
            "league": "COLOMBIA: LBP - Clausura"}
    assert basketball_live(brain, lane) is None


def test_basketball_v5_quarantines_unverified_big_first_half_gap(monkeypatch):
    import gool_bot2.basketball_v5_consensus as v5
    historical = {
        "historical_tier": "PASS_8", "historical_coefficient_available": True,
        "direction": "over", "scope": "FIRST_HALF", "line": 75.5,
        "odd": 1.793, "projected_total": 88.38,
        "model_probability": 0.80, "historical_home_hits": 9,
        "historical_away_hits": 9,
    }
    lane = {"scope": "FIRST_HALF", "market_family": "match_total",
            "line": 75.5, "over": 1.793, "under": 1.95}
    monkeypatch.setattr(v5, "dynamic_prematch_signal", lambda *a, **kw: {
        "direction": "over", "odd": 1.793, "model_probability": 0.86,
        "market_probability": 0.50, "data_quality": 0.90,
    })
    monkeypatch.delenv("GOOL_BASKETBALL_V5_MAX_UNVERIFIED_GAP_POINTS", raising=False)
    assert basketball_v5_prematch_signal(historical, lane, {}, "COLOMBIA") is None
    monkeypatch.setenv("GOOL_BASKETBALL_V5_MAX_UNVERIFIED_GAP_POINTS", "15")
    assert basketball_v5_prematch_signal(historical, lane, {}, "COLOMBIA") is not None


def test_live_funnel_never_confuses_partial_snapshots_with_full_day():
    snapshot = {"sports": {"hockey": {
        "flashscore_live_matches": [{"flashscore_event_id": "one"}],
        "flashscore_analysis_matches": [
            {"flashscore_event_id": "one", "brain_state": "WAIT",
             "live_game_stats": {"current_segment_available": False}}
        ],
        "flashscore_live": 1, "live_brain_candidates": 0,
        "xbet_live": 0, "mapped": 0, "decoded": 0, "detected": 0,
    }}}
    page = _runtime_live_audit_html(
        [snapshot], hockey_live=[{"direction": "over"}], basketball_live=[],
    )
    assert "не полная дневная воронка" in page
    assert "В сохранённых runtime-снимках нет LIVE-кандидатов" in page
    assert "Все увиденные LIVE-матчи" not in page
