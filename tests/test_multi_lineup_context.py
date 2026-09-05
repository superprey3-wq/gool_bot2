from __future__ import annotations

from gool_bot2.multi_lineup_context import apply_lineup_context
from gool_bot2.providers.fotmob_lineup_guard import lineup_summary


def _player(pid: int, name: str, rating: float | None = None):
    row = {"id": pid, "name": name}
    if rating is not None:
        row["rating"] = rating
    return row


def test_fotmob_lineup_summary_extracts_confirmed_structure():
    detail = {
        "content": {
            "lineup": {
                "lineupType": "standard",
                "source": "test",
                "homeTeam": {
                    "id": 1,
                    "name": "A",
                    "rating": 7.1,
                    "formation": "4-3-3",
                    "starters": [_player(i, f"A{i}", 6.5 + i / 100) for i in range(1, 12)],
                    "subs": [_player(100 + i, f"AS{i}") for i in range(1, 6)],
                    "unavailable": [_player(201, "AX")],
                    "averageStarterAge": 25.4,
                    "totalStarterMarketValue": 123000000,
                },
                "awayTeam": {
                    "id": 2,
                    "name": "B",
                    "formation": "4-2-3-1",
                    "starters": [_player(300 + i, f"B{i}") for i in range(1, 12)],
                    "subs": [],
                    "unavailable": [_player(401, "BX"), _player(402, "BY")],
                },
            }
        }
    }
    out = lineup_summary(detail)
    assert out["available"] is True
    assert out["total_starters"] == 22
    assert out["total_unavailable"] == 3
    assert out["home"]["formation"] == "4-3-3"
    assert out["home"]["starter_market_value"] == 123000000.0
    assert out["home"]["average_starter_rating"] is not None


def test_many_unavailable_players_shrink_only_historical_prior():
    record = {
        "match": {"minute": 60},
        "providers": {
            "fotmob": {
                "meta": {
                    "lineup_summary": {
                        "available": True,
                        "lineup_type": "standard",
                        "total_starters": 22,
                        "total_unavailable": 6,
                        "home": {"starters": 11, "unavailable": 3},
                        "away": {"starters": 11, "unavailable": 3},
                    }
                }
            }
        },
        "match_intelligence": {
            "lineup": {"available": False},
            "suitability": {"score": 0.70, "components": {"lineup": 0.55}},
            "probability_adjustment": {"strategy": "another_goal", "after": 0.83},
        },
    }
    experts = {
        "another_goal": {
            "probability": 0.83,
            "diagnostics": {
                "half_prematch_prior": {
                    "live_probability_before": 0.76,
                    "probability_after": 0.80,
                }
            },
        }
    }
    ctx = apply_lineup_context(record, experts)
    assert ctx is not None
    assert ctx["history_multiplier"] == 0.60
    # Historical +4pp is shrunk to +2.4pp, removing 1.6pp from the final P.
    assert experts["another_goal"]["probability"] == 0.814
    assert ctx["probability_delta_pp"] == -1.6
    assert record["match_intelligence"]["suitability"]["lineup_risk"] == "medium"


def test_normal_lineup_does_not_disturb_probability():
    record = {
        "match": {"minute": 20},
        "providers": {
            "fotmob": {
                "meta": {
                    "lineup_summary": {
                        "available": True,
                        "total_starters": 22,
                        "total_unavailable": 1,
                        "home": {},
                        "away": {},
                    }
                }
            }
        },
        "match_intelligence": {
            "lineup": {"available": False},
            "suitability": {"score": 0.70, "components": {"lineup": 0.55}},
            "probability_adjustment": {"strategy": "goal_before_ht"},
        },
    }
    experts = {"goal_before_ht": {"probability": 0.79, "diagnostics": {}}}
    ctx = apply_lineup_context(record, experts)
    assert ctx is not None
    assert ctx["history_multiplier"] == 1.0
    assert experts["goal_before_ht"]["probability"] == 0.79
    assert record["match_intelligence"]["suitability"]["components"]["lineup"] == 0.92
