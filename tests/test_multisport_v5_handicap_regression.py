from __future__ import annotations

from gool_bot2.basketball_v5_consensus import basketball_v5_prematch_signal
from gool_bot2.hockey_brain_v2 import prematch_signal as hockey_prematch_signal
from gool_bot2.xbet_multisport_steam import select_prematch_primary


def _historical():
    return {
        "historical_tier": "PASS_8",
        "historical_coefficient_available": True,
        "direction": "over", "line": 40.5, "odd": 1.70,
        "model_probability": .77778, "historical_home_hits": 10,
        "historical_away_hits": 10, "market_probability": .55,
        "strength": 89,
    }


def test_basketball_v5_requires_both_models_and_exact_live_bookmaker_quote(monkeypatch):
    import gool_bot2.basketball_v5_consensus as v5
    lane = {"line": 40.5, "over": 1.70, "under": 2.05, "scope": "QUARTER_1", "market_family": "match_total"}
    monkeypatch.setattr(v5, "dynamic_prematch_signal", lambda *args, **kwargs: {
        "direction": "over", "line": 40.5, "odd": 1.70,
        "model_probability": .86, "market_probability": .55, "data_quality": .92,
    })
    signal = basketball_v5_prematch_signal(_historical(), lane, {}, "KHL")
    assert signal is not None
    assert signal["brain_mode"] == "basketball_prematch_v5_consensus"
    assert signal["historical_probability_uncalibrated"] is True
    assert signal["probabilities_calibrated"] is False
    assert signal["model_probability"] < _historical()["model_probability"]
    assert signal["value_after_vig_proxy"] > .025
    assert basketball_v5_prematch_signal({**_historical(), "historical_tier": "WATCH_7"}, lane, {}, "") is None

    monkeypatch.setattr(v5, "dynamic_prematch_signal", lambda *args, **kwargs: {
        "direction": "under", "odd": 1.70, "model_probability": .9,
        "market_probability": .55, "data_quality": .92,
    })
    assert basketball_v5_prematch_signal(_historical(), lane, {}, "") is None

    monkeypatch.setattr(v5, "dynamic_prematch_signal", lambda *args, **kwargs: {
        "direction": "over", "odd": 1.71, "model_probability": .9,
        "market_probability": .55, "data_quality": .92,
    })
    assert basketball_v5_prematch_signal(_historical(), lane, {}, "") is None


def test_basketball_v5_rejects_probabilities_that_do_not_cover_real_vig(monkeypatch):
    import gool_bot2.basketball_v5_consensus as v5
    monkeypatch.setattr(v5, "dynamic_prematch_signal", lambda *args, **kwargs: {
        "direction": "over", "odd": 1.40, "model_probability": .74,
        "market_probability": .70, "data_quality": .92,
    })
    historical = {**_historical(), "odd": 1.40, "model_probability": .73}
    lane = {"line": 40.5, "over": 1.40, "under": 3.0}
    assert basketball_v5_prematch_signal(historical, lane, {}, "") is None


def _hockey_features():
    return {
        "home_recent_n": 10, "away_recent_n": 10,
        "home_gf_avg": 3.7, "home_ga_avg": 1.9,
        "away_gf_avg": 2.1, "away_ga_avg": 3.5,
        "home_venue_gf_avg": 3.9, "away_venue_gf_avg": 1.9,
        "rest_advantage_days": 1, "home_rest_days": 2, "away_rest_days": 1,
    }


def test_hockey_extreme_plus_nine_is_blocked_if_model_expects_close_game():
    lane = {
        "market_family": "handicap", "selection_side": "away",
        "line": 9.0, "odd": 1.615, "probability": .564,
    }
    assert hockey_prematch_signal(lane, _hockey_features(), "KHL") is None


def test_hockey_negative_five_remains_eligible_when_goals_support_it(monkeypatch):
    import gool_bot2.hockey_brain_v2 as hockey
    monkeypatch.setattr(hockey, "prematch_lambdas", lambda *args, **kwargs: (9.0, 1.0, 5.6))
    lane = {
        "market_family": "handicap", "selection_side": "home",
        "line": -5.0, "odd": 1.90, "probability": .65, "selection": "Ф1 -5",
    }
    signal = hockey_prematch_signal(lane, _hockey_features(), "KHL")
    assert signal is not None
    assert signal["direction"] == "home"
    assert signal["line"] == -5
    assert signal["value_after_vig_proxy"] > 0


def test_hockey_no_longer_ranks_maximum_win_probability_first():
    options = [
        ({"market_family": "handicap"}, {
            "odd": 1.80, "model_probability": .95, "edge": .08, "value_after_vig_proxy": .08,
            "line": 4.0, "data_quality": .8, "strength": 88,
        }),
        ({"market_family": "handicap"}, {
            "odd": 1.80, "model_probability": .72, "edge": .14, "value_after_vig_proxy": .16,
            "line": -1.5, "data_quality": .9, "strength": 82,
        }),
    ]
    row, signal = select_prematch_primary(options, sport="hockey")
    assert signal["line"] == -1.5


def test_signed_1xbet_handicaps_do_not_overwrite_one_another():
    from gool_bot2.xbet_multisport_markets import _handicap_lines, prematch_market_lanes
    raw = [
        {"G": 2, "T": 4, "P": -5, "C": 3.1},
        {"G": 2, "T": 5, "P": +5, "C": 1.4},
        {"G": 2, "T": 4, "P": +5, "C": 1.32},
        {"G": 2, "T": 5, "P": -5, "C": 3.8},
    ]
    decoded = _handicap_lines(raw)
    assert len(decoded) == 2
    assert {(v["home_line"], v["away_line"]) for v in decoded} == {(-5, 5), (5, -5)}
    lanes = prematch_market_lanes({"FULL_MATCH": {"handicap": decoded}}, "hockey")
    assert any(row.get("market_family") == "handicap" and row.get("selection_side") == "home" and row.get("line") == -5 for row in lanes)
    assert any(row.get("market_family") == "handicap" and row.get("selection_side") == "away" and row.get("line") == -5 for row in lanes)


def test_basketball_v5_blocks_preseason_by_default_but_allows_explicit_shadow_override(monkeypatch):
    import gool_bot2.basketball_v5_consensus as v5
    lane = {"line": 40.5, "over": 1.70, "under": 2.05, "scope": "QUARTER_1", "market_family": "match_total"}
    monkeypatch.setattr(v5, "dynamic_prematch_signal", lambda *args, **kwargs: {
        "direction": "over", "odd": 1.70, "model_probability": .86,
        "market_probability": .55, "data_quality": .92,
    })
    assert basketball_v5_prematch_signal(_historical(), lane, {}, "USA: NBA - Pre-season") is None
    monkeypatch.setenv("GOOL_BASKETBALL_V5_ALLOW_PRESEASON", "1")
    assert basketball_v5_prematch_signal(_historical(), lane, {}, "USA: NBA - Pre-season") is not None


def test_basketball_v5_does_not_promote_extreme_uncalibrated_half_totals(monkeypatch):
    import gool_bot2.basketball_v5_consensus as v5
    lane = {
        "line": 119.5, "over": 1.95, "under": 1.80,
        "scope": "FIRST_HALF", "market_family": "match_total",
    }
    hist = {
        **_historical(), "direction": "under", "odd": 1.80,
        "line": 119.5, "projected_total": 103.67,
    }
    monkeypatch.setattr(v5, "dynamic_prematch_signal", lambda *args, **kwargs: {
        "direction": "under", "odd": 1.80, "model_probability": .90,
        "market_probability": .50, "data_quality": .92,
    })
    assert basketball_v5_prematch_signal(hist, lane, {}, "NBA") is None
    monkeypatch.setenv("GOOL_BASKETBALL_V5_MAX_UNVERIFIED_GAP_POINTS", "20")
    assert basketball_v5_prematch_signal(hist, lane, {}, "NBA") is not None
