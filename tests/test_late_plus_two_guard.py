from __future__ import annotations

from gool_bot2 import live_gool_analyzer as analyzer
from gool_bot2 import storage_market_signal_worker as worker


def _record(minute: int) -> dict:
    return {
        "match": {
            "flashscore_event_id": "late2goals",
            "minute": minute,
            "home_score": 1,
            "away_score": 2,
        }
    }


def _market_override(*_args, **_kwargs) -> dict:
    return {
        "override": True,
        "value_override": True,
        "value_bet": True,
        "reason": "strong market move",
        "value_reason": "very strong value",
    }


def _rejected_analyzer(_record: dict) -> dict:
    return {
        "passed": False,
        "confidence_score": 0.68,
        "pressure_score": 1.29,
        "blocks": ["recent_threat_missing"],
    }


def test_market_can_wake_plus_two_before_override_cutoff(monkeypatch):
    monkeypatch.setattr(worker, "_ORIG_TWO_MORE", _rejected_analyzer)
    monkeypatch.setattr(worker, "_set_value", _market_override)

    result = worker._two_more(_record(59))

    assert result["passed"] is True
    assert result["market_override"] is True
    assert result["value_override"] is True
    assert result["blocks"] == []


def test_market_cannot_wake_plus_two_after_override_cutoff(monkeypatch):
    monkeypatch.setattr(worker, "_ORIG_TWO_MORE", _rejected_analyzer)
    monkeypatch.setattr(worker, "_set_value", _market_override)

    result = worker._two_more(_record(62))

    assert result["passed"] is False
    assert result["confidence_score"] == 0.68
    assert result["market_override_suppressed_late"] is True
    assert result["value_override_suppressed_late"] is True
    assert result["override_cutoff_minute"] == 60


def test_plus_two_is_hard_closed_after_65_even_with_value(monkeypatch):
    monkeypatch.setattr(worker, "_ORIG_TWO_MORE", _rejected_analyzer)
    monkeypatch.setattr(worker, "_set_value", _market_override)

    result = worker._two_more(_record(74))

    assert result["passed"] is False
    assert result["confidence_score"] is None
    assert result["market_override_suppressed"] is True
    assert result["value_override_suppressed"] is True
    assert result["hard_time_block"] == "two_more_window_closed:74>65"
    assert "two_more_window_closed:74>65" in result["blocks"]


def test_base_two_more_analyzer_is_also_closed_after_65(monkeypatch):
    monkeypatch.setenv("GOOL_TWO_MORE_MAX_MINUTE", "65")

    result = analyzer.analyze_two_more_goals(_record(74))

    assert result["passed"] is False
    assert result["confidence_score"] is None
    assert result["pressure_score"] is None
    assert result["max_signal_minute"] == 65
    assert result["hard_time_block"] == "two_more_window_closed:74>65"
