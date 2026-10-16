"""PREMATCH market competition and volatility audit (no public policy change)."""

from gool_bot2.xbet_multisport_steam import (
    SPORTS,
    MultiSportSteamWorker,
    prematch_market_comparison,
    select_prematch_primary,
)
from gool_bot2.hockey_brain_v2 import prematch_scenario_stress
from gool_bot2.full_report_export import _diagnostic_text


def _candidate(row, *, odd=1.85, edge=.12, ev=.14, quality=.9):
    return (row, {
        "selection": row.get("selection", "bet"), "line": row["line"],
        "odd": odd, "model_probability": .65, "market_probability": .53,
        "edge": edge, "value_after_vig_proxy": ev,
        "data_quality": quality, "strength": 75,
    })


def test_market_comparison_preserves_plus_minus_and_total_families(monkeypatch):
    monkeypatch.delenv("GOOL_HOCKEY_SINGLE_MIN_ODD", raising=False)
    plus = {"market_family": "handicap", "scope": "FULL_MATCH",
            "line": 2.5, "selection": "Ф2 +2.5"}
    minus = {"market_family": "handicap", "scope": "FULL_MATCH",
             "line": -1.5, "selection": "Ф1 -1.5"}
    total = {"market_family": "match_total", "scope": "FULL_MATCH",
             "line": 5.5, "selection": "ТБ 5.5"}
    individual = {"market_family": "away_total", "scope": "FULL_MATCH",
                  "line": 2.5, "selection": "ИТБ2 2.5"}
    qualified = [
        _candidate(plus, odd=1.55),
        _candidate(minus, edge=.14, ev=.18),
        _candidate(total, edge=.12, ev=.15),
    ]
    snapshot = prematch_market_comparison(
        [plus, minus, total, individual], qualified, sport="hockey",
    )
    assert snapshot["public_min_odd"] >= 1.70
    assert snapshot["offered"] == {
        "handicap_plus": 1, "handicap_minus": 1,
        "match_total": 1, "team_total": 1,
    }
    assert snapshot["brain_qualified"]["handicap_plus"] == 1
    assert snapshot["public_price_qualified"].get("handicap_plus", 0) == 0
    assert snapshot["public_price_qualified"]["handicap_minus"] == 1
    assert snapshot["public_price_qualified"]["match_total"] == 1
    assert len(snapshot["ranked_choices"]) == 2
    expected = select_prematch_primary(qualified, sport="hockey")
    assert expected is not None
    assert snapshot["ranked_choices"][0]["selection"] == expected[1]["selection"]
    assert snapshot["probabilities_calibrated"] is False


def test_basketball_comparison_explains_why_fora_is_missing():
    plus = {"market_family": "handicap", "scope": "FULL_MATCH", "line": 5.5}
    minus = {"market_family": "handicap", "scope": "FULL_MATCH", "line": -5.5}
    total = {"market_family": "match_total", "scope": "QUARTER_2", "line": 40.5}
    snapshot = prematch_market_comparison(
        [plus, minus, total], [_candidate(total, odd=1.80)], sport="basketball",
    )
    assert snapshot["offered"]["handicap_plus"] == 1
    assert snapshot["offered"]["handicap_minus"] == 1
    assert snapshot["public_price_qualified"] == {"segment_match_total": 1}
    assert snapshot["basketball_handicaps_active"] is False


def test_hockey_under_stress_becomes_weaker_when_both_teams_score_more():
    data = prematch_scenario_stress(
        lam_home=2.3, lam_away=2.5, family="match_total",
        direction="under", line=5.5, odd=1.90,
    )
    assert data["available"] and data["probabilities_calibrated"] is False
    assert len(data["scenarios"]) == 5
    baseline = data["scenarios"]["baseline"]["ev_units_uncalibrated"]
    faster = data["scenarios"]["both_high"]["ev_units_uncalibrated"]
    assert faster < baseline
    assert data["worst_ev_units_uncalibrated"] <= faster


def test_hockey_plus_handicap_stress_exposes_blowout_risk():
    data = prematch_scenario_stress(
        lam_home=3.0, lam_away=2.0, family="handicap",
        direction="away", line=1.5, odd=1.90,
    )
    assert data["scenarios"]["home_stronger"]["win"] < data["scenarios"]["baseline"]["win"]
    assert data["scenarios"]["home_stronger"]["ev_units_uncalibrated"] < data["baseline_ev_units_uncalibrated"]


def test_recent_results_produce_dispersion_with_explicit_sample_size():
    history = {
        "home_recent": [
            {"home": "Home", "away": "Other", "home_score": score,
             "away_score": 2, "timestamp": 1000+i}
            for i, score in enumerate([0, 6, 1, 6, 0, 5, 1, 7, 0, 5])
        ],
        "away_recent": [
            {"home": "Other", "away": "Away", "home_score": 2,
             "away_score": score, "timestamp": 2000+i}
            for i, score in enumerate([2, 3, 1, 2, 3, 1, 2, 3, 1, 2])
        ],
    }
    features = MultiSportSteamWorker._sport_context_features(
        history, {"home": "Home", "away": "Away", "start_ts": 1000000},
        SPORTS["hockey"],
    )
    assert features["home_gf_variance"] > features["away_gf_variance"]
    assert features["recent_total_sample_n"] == 20
    assert features["recent_total_variance"] is not None


def test_report_explicitly_marks_uncalibrated_stress_and_market_alternatives():
    details = prematch_scenario_stress(
        lam_home=2.0, lam_away=2.3, family="away_total",
        direction="under", line=3.5, odd=1.8,
    )
    result = _diagnostic_text({
        "sport": "hockey", "phase": "PREMATCH",
        "prematch_scenario_stress": details,
        "historical_scoring_dispersion": {
            "home_for": 2.1, "away_for": .8,
        },
        "prematch_market_comparison": {
            "offered": {"handicap_plus": 1, "match_total": 2},
            "public_price_qualified": {"match_total": 1},
            "ranked_choices": [
                {"selection": "ИТМ2 3.5", "odd": 1.8},
                {"selection": "ТМ 6.5", "odd": 1.92},
            ],
        },
    })
    assert "НЕ КАЛИБРОВАНО" in result
    assert "Ф+" in result
    assert "Следующий вариант:" in result
    assert "разброс шайб" in result
