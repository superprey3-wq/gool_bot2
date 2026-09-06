from __future__ import annotations

import copy
import time

from gool_bot2.brain_v3_history_trends import apply_history_trend_support, score_aware_history_trend


def _rows(total: int, n: int = 8, source: str = "365scores_recent_halves") -> list[dict]:
    out = []
    for i in range(n):
        home_score = max(0, total - 1)
        away_score = 1
        out.append(
            {
                "event_id": f"{source}-{total}-{i}",
                "home": f"Home {i}",
                "away": f"Away {i}",
                "home_score": home_score,
                "away_score": away_score,
                "timestamp": f"2026-08-{(i % 9) + 1:02d}T12:00:00Z",
                "source": source,
            }
        )
    return out


def _record(score=(1, 1), total: int = 3) -> dict:
    home_rows = _rows(total, 8)
    away_rows = _rows(total, 8, source="fotmob_team_history")
    return {
        "match": {
            "minute": 66,
            "home_score": score[0],
            "away_score": score[1],
            "home": "Home",
            "away": "Away",
        },
        "prematch_context": {
            "home_recent": home_rows,
            "away_recent": away_rows,
            "home_at_home": home_rows[:5],
            "away_away": away_rows[:5],
            "h2h": _rows(total, 4, source="365scores_recent_halves"),
            "sources": ["365scores_recent_halves", "fotmob_team_history"],
            "has_trends": True,
            "has_top_trends": True,
        },
    }


def _decision(*, probability=0.69, live_probability=0.68, live_foundation=True) -> dict:
    return {
        "version": 3,
        "active": True,
        "status": "READY",
        "period": "2H",
        "minute": 66,
        "score": [1, 1],
        "probability": probability,
        "live_probability": live_probability,
        "confidence_score": probability * 100.0,
        "confidence_cap": 0.92,
        "bet_min": 0.70,
        "ready_min": 0.60,
        "live_foundation": live_foundation,
        "sustained_pressure": True,
        "blocks": ["brain_v3_probability_below_bet"],
        "thoughts": [],
        "prematch": {"adjustment_pp": round((probability - live_probability) * 100.0, 2)},
    }


def test_one_one_over25_history_can_confirm_live_ready_into_bet():
    record = _record(score=(1, 1), total=3)
    decision = _decision(probability=0.69, live_probability=0.68, live_foundation=True)

    out = apply_history_trend_support(record, decision)

    trend = out["history_trend"]
    assert trend["available"] is True
    assert trend["next_full_total_line"] == 2.5
    assert trend["combined_over_rate"] >= 0.95
    assert trend["scores365_sample"] >= 8
    assert trend["applied_adjustment_pp"] > 0
    assert out["probability"] >= 0.70
    assert out["status"] == "BET"


def test_history_trend_never_manufactures_signal_without_live_foundation():
    record = _record(score=(1, 1), total=3)
    decision = _decision(probability=0.69, live_probability=0.68, live_foundation=False)

    out = apply_history_trend_support(record, decision)

    assert out["probability"] == 0.69
    assert out["status"] == "READY"
    assert out["history_trend"]["applied_adjustment_pp"] == 0.0
    assert out["history_trend"]["apply_reason"] == "live_foundation_required"


def test_total_prematch_and_trend_context_stays_capped_at_four_points(monkeypatch):
    monkeypatch.setenv("GOOL_BRAIN_V3_CONTEXT_MAX_ADJUST_PP", "4.0")
    record = _record(score=(1, 1), total=4)
    decision = _decision(probability=0.72, live_probability=0.68, live_foundation=True)

    out = apply_history_trend_support(record, decision)

    assert out["history_trend"]["total_context_pp"] <= 4.0
    assert out["probability"] <= 0.72


def test_zero_zero_does_not_use_over25_history_as_direct_one_goal_hint():
    trend = score_aware_history_trend(_record(score=(0, 0), total=4))
    assert trend["available"] is False
    assert trend["adjustment_pp"] == 0.0


def test_300_match_trend_load_is_small_enough_for_live_cycle():
    template_record = _record(score=(1, 1), total=3)
    template_decision = _decision(probability=0.69, live_probability=0.68, live_foundation=True)
    started = time.perf_counter()
    results = []
    for _ in range(300):
        results.append(
            apply_history_trend_support(
                copy.deepcopy(template_record),
                copy.deepcopy(template_decision),
            )["status"]
        )
    elapsed = time.perf_counter() - started

    assert len(results) == 300
    assert all(status == "BET" for status in results)
    # This path performs no network/browser work and scans only the capped recent
    # history already present in each record. Keep a generous CI ceiling so the
    # test catches accidental expensive work without becoming hardware-sensitive.
    assert elapsed < 2.0
