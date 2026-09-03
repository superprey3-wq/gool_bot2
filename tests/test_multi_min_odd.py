from __future__ import annotations

from gool_bot2.multi_router import MarketCandidate, score_candidate
from gool_bot2.value_bet_policy import ABSOLUTE_MIN_BET_ODD, attach_value


def _candidate(odd: float) -> MarketCandidate:
    return MarketCandidate(
        key="match_total:0.5",
        family="match_total",
        strategy="another_goal",
        label="ТБ 0.5",
        odd=odd,
        model_probability=0.90,
        correlation_key="any_next_goal",
        source="test",
        expert_passed=True,
        market_age_seconds=0.0,
        data_quality=1.0,
    )


def test_multi_router_has_absolute_minimum_odd_1_40():
    assert ABSOLUTE_MIN_BET_ODD == 1.40

    below = score_candidate(_candidate(1.399), minute=30)
    boundary = score_candidate(_candidate(1.40), minute=30)

    assert "price_too_low" in below.blocks
    assert "price_too_low" not in boundary.blocks
    assert boundary.eligible is True


def test_value_layer_starts_at_1_40_by_default(monkeypatch):
    monkeypatch.delenv("XBET_VALUE_MIN_ODD", raising=False)
    info = {
        "age_seconds": 0.0,
        "head": "another_goal",
        "targets": [{
            "market": "match_total",
            "line": 0.5,
            "weight": 1.0,
            "label": "ТБ 0.5",
            "selection": {"odd": 1.40, "opposite": 3.50},
        }],
    }

    result = attach_value(info, 0.90, probability_source="test")

    assert result["value_min_odd"] == 1.40
    assert result["value_price_ok"] is True
    assert result["value_bet"] is True


def test_value_minimum_cannot_be_configured_below_1_40(monkeypatch):
    monkeypatch.setenv("XBET_VALUE_MIN_ODD", "1.20")
    info = {
        "age_seconds": 0.0,
        "head": "another_goal",
        "targets": [{
            "market": "match_total",
            "line": 0.5,
            "weight": 1.0,
            "label": "ТБ 0.5",
            "selection": {"odd": 1.39, "opposite": 3.50},
        }],
    }

    result = attach_value(info, 0.90, probability_source="test")

    assert result["value_min_odd"] == 1.40
    assert result["value_price_ok"] is False
    assert result["value_bet"] is False
