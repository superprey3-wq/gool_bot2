from __future__ import annotations

from pathlib import Path

from gool_bot2.football_v5_shadow import score_grid
from gool_bot2.football_v6_consensus_shadow import (
    _poisson_over,
    evaluate_football_v6_prematch,
)


def _forecast(home=2.3, away=0.9, quality=0.85):
    return {
        "status": "READY",
        "home_lambda": home,
        "away_lambda": away,
        "quality": quality,
        "probabilities": score_grid(home, away),
    }


def _profile(home=2.2, away=1.0):
    return {"full_match": {
        "home_expected_goals": home,
        "away_expected_goals": away,
    }}


def _xbet():
    return {
        "match_totals": [
            {"line": 2.5, "over": 2.2, "under": 1.70},
            {"line": 3.0, "over": 2.4, "under": 1.62},
        ],
        "home_totals": [{"line": 1.5, "over": 2.2, "under": 1.7}],
        "away_totals": [{"line": 0.5, "over": 2.0, "under": 1.85}],
        "match_1x2": {"home": 2.2, "draw": 3.5, "away": 4.5},
        "btts": {"yes": 2.3, "no": 1.60},
    }


def test_v6_prices_all_safe_market_families_and_filters_integer_lines():
    result = evaluate_football_v6_prematch(
        _forecast(), _profile(), _xbet(), event_id="f-123"
    )
    assert result["model"] == "football_v6_consensus_shadow"
    assert result["probabilities_calibrated"] is False
    assert result["published"] is False
    assert result["quoted_markets_checked"] > 5
    families = {c["market"] for c in result["candidates"]}
    assert "match_total" in families
    assert "match_1x2" in families or "home_total" in families or "btts" in families
    assert not any(c["selection"].endswith("3") for c in result["candidates"] if c["market"]=="match_total")
    assert all(c["odd"] >= 1.4 for c in result["candidates"])
    assert all(c["market_shrunk_probability_heuristic"] < 1 for c in result["candidates"])
    assert _poisson_over(3.1, 2.25) is None
    assert _poisson_over(3.1, 2.0) is None


def test_v6_cannot_create_confidence_when_independent_profile_is_missing():
    result = evaluate_football_v6_prematch(
        _forecast(), {}, _xbet(), event_id="f-123"
    )
    assert result["status"] == "WAIT"
    assert result["reason"] == "independent_profile_unavailable"


def test_v6_rejects_missing_v5_or_weak_history():
    missing = evaluate_football_v6_prematch(
        {**_forecast(), "status": "WAIT_HISTORY"}, _profile(), _xbet(), event_id="123"
    )
    assert missing["reason"] == "v5_history_unavailable"
    weak = evaluate_football_v6_prematch(
        _forecast(quality=.42), _profile(), _xbet(), event_id="123"
    )
    assert weak["reason"] == "low_data_quality"


def test_v6_has_both_over_and_under_and_does_not_alter_active_pick():
    quote = _xbet()
    original = [dict(quote_row) for quote_row in quote["match_totals"]]
    active = [{"market": "match_total", "selection": "under 2.5"}]
    result = evaluate_football_v6_prematch(
        _forecast(), _profile(), quote, event_id="123", active_picks=active
    )
    assert result["active_v5_count"] == 1
    assert quote["match_totals"] == original
    assert active == [{"market": "match_total", "selection": "under 2.5"}]
    assert "telegram_sent" not in result
    assert result["status"] in {"WAIT", "PASS_SHADOW"}


def test_v6_preserves_minimum_one_forty_without_odds_ceiling():
    quote = {"match_totals": [
        {"line": 1.5, "over": 1.39, "under": 8.0},
        {"line": 2.5, "over": 6.0, "under": 1.10},
    ]}
    result = evaluate_football_v6_prematch(
        _forecast(), _profile(), quote, event_id="f-123"
    )
    assert result["quoted_markets_checked"] > 0
    assert all(row["odd"] >= 1.40 for row in result["candidates"])
    assert any(row["odd"] >= 6.0 for row in result["candidates"])


def test_v6_is_integrated_in_real_prematch_code_and_first_snapshot_journal():
    script = (Path(__file__).resolve().parents[1] / "scripts/gool_flashscore_today.py").read_text("utf-8")
    assert "evaluate_football_v6_prematch(" in script
    assert "GOOL_FOOTBALL_V6_SHADOW_ENABLED" in script
    assert 'runtime/"football_v6"' in script
    assert "append_first_snapshots(v6_path,football_v6_rows)" in script
    assert "production_v5_picks(" in script
