from __future__ import annotations

import json
import time

from gool_bot2.brain_v3_browser_support import apply_browser_support


def _record() -> dict:
    return {
        "match": {
            "flashscore_event_id": "fs-trend",
            "home": "Home",
            "away": "Away",
            "minute": 68,
            "home_score": 1,
            "away_score": 1,
        },
        "providers": {"flashscore": {"stats": {}}},
    }


def _write_browser(path, *, percentage: float = 0.80) -> None:
    path.write_text(
        json.dumps({
            "matches": {
                "fs-trend": {
                    "captured_epoch": time.time(),
                    "minute": 68,
                    "score": [1, 1],
                    "scores365_game_id": "365-trend",
                    "stats": {"shots_on_target": [4, 3]},
                    "trends": [
                        {
                            "text": "Over 2.5 Goals - 8/10 Last Matches",
                            "cause": "Over 2.5 Goals",
                            "percentage": percentage,
                            "lineTypeId": 3,
                            "isGeneralGameBet": True,
                        }
                    ],
                }
            }
        }),
        "utf-8",
    )


def _decision(*, live_foundation: bool = True, sustained: bool = True) -> dict:
    return {
        "active": True,
        "period": "2H",
        "strategy": "another_goal",
        "probability": 0.695,
        "confidence_score": 69.5,
        "confidence_cap": 0.92,
        "live_foundation": live_foundation,
        "sustained_pressure": sustained,
        "bet_min": 0.70,
        "ready_min": 0.60,
        "status": "READY",
        "blocks": ["brain_v3_probability_below_bet"],
        "prematch": {"adjustment_pp": 1.5},
        "external_trends": {"effective_adjustment_pp": 0.8},
        "thoughts": [],
    }


def test_score_relevant_365_trend_can_confirm_live_bet(monkeypatch, tmp_path):
    path = tmp_path / "browser.json"
    _write_browser(path)
    monkeypatch.setenv("GOOL_BROWSER_CONTEXT_PATH", str(path))
    monkeypatch.setenv("GOOL_BROWSER_CONTEXT_TTL_SECONDS", "180")

    experts = {
        "another_goal": {
            "source": "brain_v3:another_goal",
            "probability": 0.695,
            "state": "BORDERLINE",
            "passed": False,
        }
    }
    decision = apply_browser_support(_record(), experts, _decision())

    support = decision["browser365"]
    assert support["market_hint"] == "over_2_5"
    assert support["over_rate"] == 0.8
    assert 0.0 < support["effective_adjustment_pp"] <= 1.25
    assert support["combined_history_adjustment_pp"] <= 4.0
    assert decision["probability"] >= 0.70
    assert decision["status"] == "BET"
    assert experts["another_goal"]["passed"] is True


def test_browser_trend_never_creates_bet_without_live_foundation(monkeypatch, tmp_path):
    path = tmp_path / "browser.json"
    _write_browser(path)
    monkeypatch.setenv("GOOL_BROWSER_CONTEXT_PATH", str(path))

    experts = {
        "another_goal": {
            "source": "brain_v3:another_goal",
            "probability": 0.695,
            "state": "BORDERLINE",
            "passed": False,
        }
    }
    decision = apply_browser_support(
        _record(),
        experts,
        _decision(live_foundation=False, sustained=False),
    )

    assert decision["browser365"]["effective_adjustment_pp"] > 0
    assert decision["status"] == "WATCH"
    assert experts["another_goal"]["passed"] is False
