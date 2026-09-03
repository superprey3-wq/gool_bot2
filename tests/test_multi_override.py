from __future__ import annotations

from datetime import datetime, timezone

from gool_bot2.multi_router import analyze_multi_match


def _fresh() -> str:
    return datetime.now(timezone.utc).isoformat()


def test_soft_gool_wait_can_be_revived_by_verified_market_override():
    match = {"minute": 64, "home_score": 1, "away_score": 1, "is_finished": False}
    market = {
        "score_home": 1,
        "score_away": 1,
        "captured_at": _fresh(),
        "markets": {
            "match_total": [{"line": 2.5, "over": 2.05, "under": 1.70}],
            "home_total": [{"line": 1.5, "over": 4.76, "under": 1.15}],
            "away_total": [],
            "btts": {"yes": 1.20, "no": 4.00},
        },
        "pressure": {
            "home_total:1.5": {
                "prob_delta_pp": 6.4,
                "one_way_moves": 5,
                "old_odd": 7.09,
            },
        },
    }
    experts = {
        "home_goal": {
            "probability": 0.50,
            "passed": False,
            "blocks": ["side_pressure_low"],
            "source": "shadow:home_goal",
        }
    }

    decision = analyze_multi_match(match, market, experts, data_quality=0.9)

    assert decision.status == "BET"
    assert decision.winner is not None
    assert decision.winner.label == "ИТБ1 1.5"
    assert decision.winner.expert_passed is False
    assert decision.winner.market_override is True
    assert "market_override" in decision.winner.reason_tags


def test_normal_market_cannot_revive_gool_wait():
    match = {"minute": 55, "home_score": 0, "away_score": 0, "is_finished": False}
    market = {
        "score_home": 0,
        "score_away": 0,
        "captured_at": _fresh(),
        "markets": {
            "match_total": [{"line": 0.5, "over": 1.55, "under": 2.30}],
            "home_total": [],
            "away_total": [],
            "btts": {},
        },
        "pressure": {"match_total:0.5": {"prob_delta_pp": 1.0, "one_way_moves": 0}},
    }
    experts = {
        "another_goal": {
            "probability": 0.66,
            "passed": False,
            "blocks": ["live_pressure_low"],
            "source": "model",
        }
    }

    decision = analyze_multi_match(match, market, experts, data_quality=0.9)

    assert decision.status == "WAIT"
    assert decision.winner is None
    assert any("gool_wait_without_verified_override" in row.blocks for row in decision.rejected)


def test_hard_time_window_cannot_be_overridden():
    match = {"minute": 76, "home_score": 1, "away_score": 1, "is_finished": False}
    market = {
        "score_home": 1,
        "score_away": 1,
        "captured_at": _fresh(),
        "markets": {
            "match_total": [{"line": 2.5, "over": 2.40, "under": 1.52}],
            "home_total": [{"line": 1.5, "over": 4.20, "under": 1.20}],
            "away_total": [],
            "btts": {},
        },
        "pressure": {
            "home_total:1.5": {"prob_delta_pp": 9.0, "one_way_moves": 4},
        },
    }
    experts = {
        "home_goal": {
            "probability": 0.55,
            "passed": False,
            "blocks": ["side_pressure_low"],
            "source": "shadow:home_goal",
        }
    }

    decision = analyze_multi_match(match, market, experts, data_quality=0.9)

    assert decision.status == "WAIT"
    assert decision.rejected
    assert any(any(block.startswith("entry_window_closed") for block in row.blocks) for row in decision.rejected)


def test_score_desync_is_hard_wait():
    match = {"minute": 50, "home_score": 1, "away_score": 0, "is_finished": False}
    market = {
        "score_home": 0,
        "score_away": 0,
        "captured_at": _fresh(),
        "markets": {
            "match_total": [{"line": 0.5, "over": 1.50, "under": 2.40}],
            "home_total": [],
            "away_total": [],
            "btts": {},
        },
        "pressure": {},
    }
    experts = {"another_goal": {"probability": 0.80, "passed": True}}

    decision = analyze_multi_match(match, market, experts, data_quality=1.0)

    assert decision.status == "WAIT"
    assert decision.winner is None
