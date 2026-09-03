from __future__ import annotations

from gool_bot2 import multi_analysis_view
from gool_bot2.multi_experts import build_expert_snapshot


def _another_goal_expert() -> dict:
    return {
        "probability": 0.87,
        "metric": "probability",
        "passed": False,
        "blocks": ["live:5m=0.62<0.95", "live:no_recent_threat"],
        "diagnostics": {
            "prematch": {
                "available": True,
                "passed": True,
                "score": 0.63,
                "minimum": 0.55,
                "combined_avg_total": 2.48,
                "blocks": [],
            },
            "live": {
                "available": True,
                "passed": False,
                "pressure": 0.76,
                "cumulative": 0.91,
                "pressure_5m": 0.62,
                "pressure_10m": 0.84,
                "minimum_cumulative": 0.85,
                "minimum_5m": 0.95,
                "minimum_10m": 0.90,
                "blocks": ["5m=0.62<0.95", "no_recent_threat"],
            },
        },
    }


def test_expert_snapshot_marks_probability_vs_confidence_and_keeps_gool_reasons():
    snapshot = build_expert_snapshot(
        model_result={
            "trained_probability": {"another_goal": 0.87, "goal_before_ht": 0.55},
            "gool_analyzer": {
                "another_goal": {
                    "required": True,
                    "passed": False,
                    "details": {
                        "prematch": {
                            "passed": True,
                            "score": 0.63,
                            "minimum": 0.55,
                            "combined_avg_total": 2.48,
                            "home_form": {"matches": 10, "avg_total": 2.6},
                            "away_form": {"matches": 10, "avg_total": 2.36},
                            "blocks": [],
                        },
                        "live": {
                            "passed": False,
                            "combined_pressure": 0.76,
                            "cumulative_pressure": 0.91,
                            "pressure_5m": 0.62,
                            "pressure_10m": 0.84,
                            "minimum_cumulative": 0.85,
                            "minimum_5m": 0.95,
                            "minimum_10m": 0.90,
                            "blocks": ["5m=0.62<0.95", "no_recent_threat"],
                        },
                    },
                }
            },
        },
        two_more_analysis={
            "confidence_score": 0.50,
            "passed": False,
            "pressure_score": 0.80,
            "minimum": 1.15,
            "recent_ready": True,
            "recent_threat": False,
            "quality_threat": False,
        },
        home_goal_analysis={
            "confidence_score": 0.50,
            "passed": False,
            "pressure_score": 0.70,
            "minimum": 0.92,
            "blocks": ["side_pressure_low"],
        },
    )

    assert snapshot["another_goal"]["metric"] == "probability"
    assert snapshot["another_goal"]["probability"] == 0.87
    assert "live:5m=0.62<0.95" in snapshot["another_goal"]["blocks"]
    assert snapshot["another_goal"]["diagnostics"]["prematch"]["score"] == 0.63
    assert snapshot["two_more_goals"]["metric"] == "confidence"
    assert "no_recent_threat" in snapshot["two_more_goals"]["blocks"]
    assert snapshot["home_goal"]["metric"] == "confidence"


def test_analysis_view_explains_price_value_and_gool_wait(monkeypatch):
    row = {
        "match_id": "m1",
        "home": "Vinotinto",
        "away": "San Antonio",
        "minute": 14,
        "score": [0, 0],
        "data_quality": 0.72,
        "experts": {
            "another_goal": _another_goal_expert(),
            "two_more_goals": {
                "probability": 0.50,
                "metric": "confidence",
                "passed": False,
                "blocks": ["pressure=0.80<1.15", "no_recent_threat"],
                "pressure_score": 0.80,
                "minimum": 1.15,
            },
        },
        "context": {
            "prematch": {
                "available": True,
                "home_recent": 10,
                "away_recent": 10,
                "home_at_home": 6,
                "away_away": 5,
                "h2h": 2,
            },
            "live": {"xg_total": 0.12, "shots_total": 2, "sot_total": 1},
        },
        "market": {"available": True, "age_seconds": 8, "targets": {}},
        "router": {
            "status": "WAIT",
            "winner": None,
            "reason": "generic",
            "rejected": [
                {
                    "label": "ТБ 0.5",
                    "family": "match_total",
                    "strategy": "another_goal",
                    "odd": 1.27,
                    "rating": 68.0,
                    "model_probability": 0.87,
                    "market_probability": 0.80,
                    "expected_roi": -0.015,
                    "value_edge_pp": 7.0,
                    "market_pressure_pp": 0.0,
                    "data_quality": 0.72,
                    "market_age_seconds": 8.0,
                    "blocks": ["price_too_low", "no_positive_value", "gool_wait_without_verified_override"],
                }
            ],
        },
    }
    monkeypatch.setattr(multi_analysis_view, "_latest_analysis", lambda: {"m1": row})

    text = multi_analysis_view.analysis_text()

    assert "GOOL MULTI · КРАТКИЙ ОТЧЁТ" in text
    assert "Vinotinto — San Antonio" in text
    assert "🎯 Ещё 1 гол: ТБ 0.5 @ 1.27" in text
    assert "🧠 Ещё гол: 87% · GOOL пока не подтверждает" in text
    assert "📊 Игра: xG 0.12 · удары 2 · в створ 1" in text
    assert "⛔ Почему ждём: мало давления, давно нет опасных атак, слишком низкий кэф" in text
    assert len(text) < 4096


def test_analysis_view_explains_event_repricing_guard(monkeypatch):
    row = {
        "match_id": "m2",
        "home": "Home",
        "away": "Away",
        "minute": 61,
        "score": [1, 1],
        "data_quality": 0.9,
        "experts": {"another_goal": {"probability": 0.79, "metric": "probability", "passed": True}},
        "context": {"prematch": {"available": False}, "live": {}},
        "market": {
            "available": True,
            "repricing_guard": True,
            "repricing_guard_reason": "EVENT_REPRICE_ODDS_SHOCK",
            "targets": {},
        },
        "router": {"status": "WAIT", "winner": None, "rejected": [], "reason": "generic"},
    }
    monkeypatch.setattr(multi_analysis_view, "_latest_analysis", lambda: {"m2": row})

    text = multi_analysis_view.analysis_text()

    assert "🧠 Ещё гол: 79% · GOOL подтверждает" in text
    assert "⛔ Почему ждём: кэфы резко дёрнулись — ждём стабилизацию" in text


def test_analysis_view_handles_missing_goal_line_compactly(monkeypatch):
    row = {
        "match_id": "m3",
        "home": "Home",
        "away": "Away",
        "minute": 28,
        "score": [0, 0],
        "data_quality": 0.8,
        "experts": {"another_goal": {"probability": 0.81, "metric": "probability", "passed": True}},
        "context": {"prematch": {"available": True}, "live": {}},
        "market": {
            "available": True,
            "targets": {"another_goal": {"label": "ТБ 0.5", "available": False, "odd": None}},
        },
        "router": {"status": "WAIT", "winner": None, "rejected": [], "reason": "generic"},
    }
    monkeypatch.setattr(multi_analysis_view, "_latest_analysis", lambda: {"m3": row})

    text = multi_analysis_view.analysis_text()

    assert "🧠 Ещё гол: 81% · GOOL подтверждает" in text
    assert "⛔ Почему ждём: GOOL пока не видит достаточно сильной игры для ставки" in text
