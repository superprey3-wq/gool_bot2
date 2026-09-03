from __future__ import annotations

from datetime import datetime, timezone

from gool_bot2.multi_router import analyze_multi_match, build_goal_market_candidates


def _fresh() -> str:
    return datetime.now(timezone.utc).isoformat()


def _first_half_market() -> dict:
    return {
        "score_home": 0,
        "score_away": 0,
        "captured_at": _fresh(),
        "markets": {
            "match_total": [],
            "first_half_total": [{"line": 0.5, "over": 1.90, "under": 1.85}],
            "home_total": [],
            "away_total": [],
            "btts": {},
        },
        "pressure": {
            "first_half_total:0.5": {
                "prob_delta_pp": 0.0,
                "one_way_moves": 0,
                "old_odd": 1.90,
            }
        },
    }


def test_first_half_value_uses_price_when_selection_has_no_precomputed_probability():
    experts = {
        "goal_before_ht": {
            "probability": 0.75,
            "passed": False,
            "blocks": ["live_pressure_low"],
            "source": "model:blended:goal_before_ht",
        }
    }

    candidates = build_goal_market_candidates(
        {"minute": 30, "home_score": 0, "away_score": 0, "is_finished": False},
        _first_half_market(),
        experts,
        data_quality=0.90,
    )

    assert len(candidates) == 1
    candidate = candidates[0]
    assert candidate.strategy == "goal_before_ht"
    assert candidate.market_level == "NEUTRAL"
    assert candidate.expert_passed is False
    assert candidate.value_override is True


def test_first_half_soft_wait_can_be_revived_by_value_override():
    decision = analyze_multi_match(
        {"minute": 30, "home_score": 0, "away_score": 0, "is_finished": False},
        _first_half_market(),
        {
            "goal_before_ht": {
                "probability": 0.75,
                "passed": False,
                "blocks": ["live_pressure_low"],
                "source": "model:blended:goal_before_ht",
            }
        },
        data_quality=0.90,
    )

    assert decision.status == "BET"
    assert decision.winner is not None
    assert decision.winner.strategy == "goal_before_ht"
    assert decision.winner.value_override is True
    assert decision.winner.market_level == "NEUTRAL"
    assert "value_override" in decision.winner.reason_tags
