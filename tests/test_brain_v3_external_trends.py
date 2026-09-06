from __future__ import annotations

import time

from gool_bot2.brain_v3_external_trends import apply_external_trend_context, build_external_trend_context


def _history(over: bool = True, n: int = 8) -> list[dict]:
    rows = []
    for i in range(n):
        if over:
            hs, aws = ((2, 1) if i % 2 == 0 else (1, 2))
        else:
            hs, aws = ((1, 0) if i % 2 == 0 else (0, 1))
        rows.append({
            "event_id": f"hist-{over}-{i}",
            "home": f"H{i}",
            "away": f"A{i}",
            "home_score": hs,
            "away_score": aws,
            "timestamp": f"2026-08-{i+1:02d}",
            "source": "365scores_recent_halves",
        })
    return rows


def _record(score=(1, 1), over=True) -> dict:
    return {
        "match": {
            "flashscore_event_id": "trend-test",
            "home": "Home",
            "away": "Away",
            "minute": 68,
            "home_score": score[0],
            "away_score": score[1],
        },
        "prematch_context": {
            "home_recent": _history(over),
            "away_recent": _history(over),
            "h2h": _history(over, 4),
            "has_trends": True,
        },
    }


def _decision(*, live_foundation=True, period="2H", probability=0.69, prematch_pp=1.0) -> dict:
    return {
        "version": 3,
        "active": True,
        "strategy": "another_goal" if period == "2H" else "goal_before_ht",
        "period": period,
        "status": "READY",
        "probability": probability,
        "confidence_score": probability * 100,
        "confidence_cap": 0.92,
        "bet_min": 0.70,
        "ready_min": 0.60,
        "live_foundation": live_foundation,
        "sustained_pressure": True,
        "blocks": ["brain_v3_probability_below_bet"],
        "thoughts": [],
        "prematch": {"adjustment_pp": prematch_pp, "label": "supportive"},
    }


def _experts() -> dict:
    return {
        "another_goal": {
            "probability": 0.69,
            "source": "brain_v3:state_machine",
            "passed": False,
            "state": "BORDERLINE",
            "diagnostics": {},
        }
    }


def test_score_1_1_maps_to_over_2_5_and_can_nudge_ready_live_case() -> None:
    record = _record(score=(1, 1), over=True)
    decision = _decision(probability=0.69)
    experts = _experts()
    out = apply_external_trend_context(record, experts, decision)
    trend = out["external_trends"]
    assert trend["market_hint"] == "over_2_5"
    assert trend["rate"] >= 0.90
    assert 0 < trend["effective_adjustment_pp"] <= 1.5
    assert out["status"] == "BET"
    assert experts["another_goal"]["passed"] is True


def test_strong_trend_never_creates_live_foundation() -> None:
    record = _record(score=(1, 1), over=True)
    decision = _decision(live_foundation=False, probability=0.69)
    out = apply_external_trend_context(record, _experts(), decision)
    assert out["external_trends"]["rate"] >= 0.90
    assert out["status"] == "WATCH"
    assert out["live_foundation"] is False


def test_prematch_plus_trend_share_one_four_point_cap() -> None:
    record = _record(score=(1, 1), over=True)
    decision = _decision(probability=0.68, prematch_pp=3.5)
    out = apply_external_trend_context(record, _experts(), decision)
    trend = out["external_trends"]
    assert trend["requested_adjustment_pp"] > 0.5
    assert trend["effective_adjustment_pp"] == 0.5
    assert trend["combined_history_adjustment_pp"] == 4.0


def test_full_match_total_trend_is_not_used_for_first_half() -> None:
    record = _record(score=(0, 0), over=True)
    trend = build_external_trend_context(record, _decision(period="1H"))
    assert trend["available"] is False
    assert trend["adjustment_pp"] == 0.0


def test_low_scoring_history_is_only_small_caution() -> None:
    record = _record(score=(1, 1), over=False)
    out = apply_external_trend_context(record, _experts(), _decision(probability=0.72, prematch_pp=0.0))
    trend = out["external_trends"]
    assert trend["label"] == "caution"
    assert -1.0 <= trend["effective_adjustment_pp"] < 0


def test_250_match_trend_layer_is_lightweight_without_network_fanout() -> None:
    started = time.perf_counter()
    for i in range(250):
        record = _record(score=(1, 1), over=(i % 3 != 0))
        record["match"]["flashscore_event_id"] = f"stress-{i}"
        apply_external_trend_context(record, _experts(), _decision(probability=0.66))
    elapsed = time.perf_counter() - started
    # This is deliberately generous for shared CI runners. The trend calculation
    # is pure in-memory work; 250 live matches must not imply 250 browser sessions.
    assert elapsed < 5.0
