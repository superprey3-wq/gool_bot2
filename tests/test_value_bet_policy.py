from __future__ import annotations

from datetime import datetime, timezone

from gool_bot2.value_bet_policy import attach_value, is_value_row


def _info(prob=0.58, odd=1.72, age=8.0):
    return {
        "head": "another_goal",
        "available": True,
        "age_seconds": age,
        "targets": [
            {
                "market": "match_total",
                "line": 2.5,
                "label": "ТБ 2.5",
                "weight": 1.0,
                "selection": {"prob": prob, "odd": odd},
            }
        ],
    }


def test_strong_value_can_override_soft_filters(monkeypatch):
    monkeypatch.setenv("XBET_VALUE_STRONG_EDGE_PP", "8")
    monkeypatch.setenv("XBET_VALUE_OVERRIDE_MIN_MODEL_PROBABILITY", "0.65")
    result = attach_value(_info(prob=0.58, odd=1.72), 0.72)
    assert result["value_bet"] is True
    assert result["value_override"] is True
    assert result["value_level"] == "VERY_STRONG_VALUE"
    assert result["value_edge_pp"] == 14.0


def test_small_edge_is_displayed_but_not_a_value_bet():
    result = attach_value(_info(prob=0.66, odd=1.50), 0.69)
    assert result["value_bet"] is False
    assert result["value_override"] is False
    assert result["value_level"] == "NO_VALUE"


def test_stale_market_cannot_trigger_value():
    result = attach_value(_info(prob=0.50, odd=1.90, age=120), 0.75)
    assert result["value_edge_pp"] == 25.0
    assert result["value_bet"] is False
    assert result["value_override"] is False
    assert result["value_fresh"] is False


def test_bad_price_cannot_trigger_value():
    result = attach_value(_info(prob=0.40, odd=1.20), 0.70)
    assert result["value_edge_pp"] == 30.0
    assert result["value_bet"] is False
    assert result["value_price_ok"] is False


def test_btts_value_uses_btts_yes_not_individual_total():
    info = {
        "head": "both_teams_to_score",
        "available": True,
        "age_seconds": 5,
        "targets": [
            {"market": "home_total", "label": "ИТБ1 0.5", "weight": 0.42, "selection": {"prob": 0.40, "odd": 2.1}},
            {"market": "btts_yes", "label": "ОЗ — Да", "weight": 0.58, "selection": {"prob": 0.57, "odd": 1.75}},
        ],
    }
    result = attach_value(info, 0.68)
    assert result["value_market_label"] == "ОЗ — Да"
    assert result["value_market_probability"] == 0.57
    assert result["value_edge_pp"] == 11.0


def test_value_rows_are_counted_separately():
    assert is_value_row({"signal_source": "xbet_value_bet"}) is True
    assert is_value_row({"xbet_market": {"value_bet": True}}) is True
    assert is_value_row({"signal_source": "gool"}) is False
