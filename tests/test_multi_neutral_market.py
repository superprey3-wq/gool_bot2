from __future__ import annotations

from datetime import datetime, timezone

from gool_bot2.multi_router import analyze_multi_match


def _fresh() -> str:
    return datetime.now(timezone.utc).isoformat()


def _market(odd: float = 1.45) -> dict:
    return {
        "score_home": 0,
        "score_away": 0,
        "captured_at": _fresh(),
        "markets": {
            "match_total": [{"line": 0.5, "over": odd, "under": 2.70}],
            "first_half_total": [],
            "home_total": [],
            "away_total": [],
            "btts": {},
        },
        "pressure": {"match_total:0.5": {"prob_delta_pp": 0.0, "one_way_moves": 0}},
    }


def test_neutral_1xbet_does_not_kill_a_gool_passed_high_probability_signal():
    decision = analyze_multi_match(
        {"minute": 26, "home_score": 0, "away_score": 0, "is_finished": False},
        _market(),
        {
            "another_goal": {
                "probability": 0.895,
                "passed": True,
                "blocks": [],
                "source": "model:blended:another_goal",
            }
        },
        data_quality=0.9,
    )

    assert decision.status == "BET"
    assert decision.winner is not None
    assert decision.winner.strategy == "another_goal"
    assert decision.winner.market_level == "NEUTRAL"
    assert decision.winner.expert_passed is True
    assert "gool_wait_without_verified_override" not in decision.winner.blocks


def test_neutral_1xbet_cannot_revive_a_real_gool_wait_without_override():
    decision = analyze_multi_match(
        {"minute": 26, "home_score": 0, "away_score": 0, "is_finished": False},
        _market(),
        {
            "another_goal": {
                "probability": 0.895,
                "passed": False,
                "blocks": ["live_pressure_low"],
                "source": "model:blended:another_goal",
            }
        },
        data_quality=0.9,
    )

    assert decision.status == "WAIT"
    assert decision.winner is None
    assert any("gool_wait_without_verified_override" in row.blocks for row in decision.rejected)
