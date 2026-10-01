from __future__ import annotations

from gool_bot2.halftime_second_half import (
    choose_second_half_market,
    evaluate_halftime_second_half,
)


def _record(score=(0, 0), xg=(0.8, 0.6), shots=(7, 6), sot=(3, 2), big=(1, 1)):
    return {
        "match": {
            "home": "A", "away": "B", "home_score": score[0], "away_score": score[1],
            "minute": 45, "is_halftime": True,
        },
        "providers": {
            "flashscore": {
                "stats": {
                    "xg": xg,
                    "shots": shots,
                    "shots_on_target": sot,
                    "big_chances": big,
                    "touches_box": (14, 10),
                }
            },
            "fotmob": {"stats": {"xg": xg}},
        },
        "consensus": {"xg": xg, "provider_count": 2},
    }


def _profile():
    return {
        "first_half": {"expected_total": 1.05, "pair_sample": 6},
        "second_half": {
            "expected_total": 1.45,
            "home_expected_goals": 0.80,
            "away_expected_goals": 0.65,
            "pair_sample": 6,
            "over": {"0.5": 0.72, "1.5": 0.43, "2.5": 0.18},
        },
        "full_match": {"expected_total": 2.50},
    }


def test_open_first_half_projects_second_half_goal():
    out = evaluate_halftime_second_half(_record(), prematch_profile=_profile())
    assert out.expected_goals_2h > 1.2
    assert out.probability_goal_2h > 0.70
    assert out.confidence >= 0.58


def test_large_margin_reduces_projection():
    open_game = evaluate_halftime_second_half(_record(score=(0, 0)), prematch_profile=_profile())
    large = evaluate_halftime_second_half(_record(score=(3, 0)), prematch_profile=_profile())
    assert large.expected_goals_2h < open_game.expected_goals_2h


def test_real_over_price_can_qualify():
    analysis = evaluate_halftime_second_half(_record(), prematch_profile=_profile())
    markets = {
        "match_total": [
            {"line": 0.5, "over": 1.55, "under": 2.55},
            {"line": 1.5, "over": 2.45, "under": 1.55},
        ]
    }
    pick = choose_second_half_market(analysis, markets)
    assert pick.decision in {"BET", "LEAN", "SKIP"}
    if pick.decision == "BET":
        assert pick.odds is not None
        assert pick.expected_value is not None and pick.expected_value > 0


def test_no_market_returns_lean_or_skip_not_fake_price():
    analysis = evaluate_halftime_second_half(_record(), prematch_profile=_profile())
    pick = choose_second_half_market(analysis, None)
    assert pick.odds is None
    assert pick.decision in {"LEAN", "SKIP"}


def test_proxy_only_without_second_half_history_cannot_bet():
    record = _record()
    # Remove provider xG while retaining real shots/SOT/big-chance statistics.
    record["providers"]["flashscore"]["stats"].pop("xg", None)
    record["providers"]["fotmob"]["stats"].pop("xg", None)
    record["consensus"].pop("xg", None)
    profile = {
        "second_half": {"pair_sample": 0},
        "full_match": {"expected_total": 2.30},
    }
    analysis = evaluate_halftime_second_half(record, prematch_profile=profile)
    assert analysis.first_half_xg_source == "attack_proxy"
    assert analysis.prematch_second_half_sample == 0

    markets = {
        "match_total": [
            {"line": 1.5, "over": 2.30, "under": 1.62},
            {"line": 2.5, "over": 3.00, "under": 1.48},
        ]
    }
    pick = choose_second_half_market(analysis, markets)
    assert pick.decision == "LEAN"
    assert pick.odds is None
    assert "UNCALIBRATED_HT" in pick.reason
