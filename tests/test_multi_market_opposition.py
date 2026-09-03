from __future__ import annotations

from datetime import datetime, timezone

from gool_bot2.multi_router import analyze_multi_match


def _market(delta_pp: float) -> dict:
    return {
        "score_home": 0,
        "score_away": 0,
        "captured_at": datetime.now(timezone.utc).isoformat(),
        "markets": {
            "match_total": [{"line": 0.5, "over": 1.70, "under": 2.10}],
            "first_half_total": [],
            "home_total": [],
            "away_total": [],
            "btts": {},
        },
        "pressure": {
            "match_total:0.5": {
                "prob_delta_pp": delta_pp,
                "one_way_moves": 0,
                "old_odd": 1.45 if delta_pp < 0 else 1.70,
            }
        },
    }


def _decision(probability: float, delta_pp: float):
    return analyze_multi_match(
        {"minute": 34, "home_score": 0, "away_score": 0, "is_finished": False},
        _market(delta_pp),
        {
            "another_goal": {
                "probability": probability,
                "passed": True,
                "blocks": [],
                "source": "model:blended:another_goal",
            }
        },
        data_quality=0.90,
    )


def test_strong_market_opposition_can_downgrade_borderline_gool_bet_to_wait():
    neutral = _decision(0.70, 0.0)
    opposed = _decision(0.70, -8.0)

    assert neutral.status == "BET"
    assert neutral.winner is not None
    assert opposed.status == "WAIT"
    assert opposed.winner is None
    row = next(row for row in opposed.rejected if row.strategy == "another_goal")
    assert row.market_pressure_pp == -8.0
    assert "strong_market_opposition" in row.reason_tags
    assert "router_rating_below_62" in row.blocks
    assert row.rating < neutral.winner.rating


def test_exceptionally_strong_gool_can_survive_opposition_but_is_penalized():
    neutral = _decision(0.895, 0.0)
    opposed = _decision(0.895, -8.0)

    assert neutral.status == "BET"
    assert opposed.status == "BET"
    assert neutral.winner is not None and opposed.winner is not None
    assert opposed.winner.market_pressure_pp == -8.0
    assert "strong_market_opposition" in opposed.winner.reason_tags
    assert opposed.winner.rating < neutral.winner.rating
