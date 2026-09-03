from gool_bot2 import multi_analysis_view


def _wait_row(*, guard_reason=None):
    market = {
        "available": True,
        "targets": {
            "another_goal": {
                "label": "ТБ 2.5",
                "available": True,
                "odd": 1.82,
            }
        },
        "repricing_guard": bool(guard_reason),
        "repricing_guard_reason": guard_reason,
        "score_desync": False,
        "timeline_score_desync": False,
    }
    if guard_reason:
        market["targets"]["another_goal"]["available"] = False
        market["targets"]["another_goal"]["odd"] = None

    return {
        "home": "Vinotinto",
        "away": "San Antonio",
        "minute": 69,
        "score": [1, 1],
        "context": {
            "live": {
                "xg_total": 0.25,
                "shots_total": 3,
                "sot_total": 2,
                "big_chances_total": 0,
            }
        },
        "market": market,
        "experts": {
            "another_goal": {
                "probability": 0.44,
                "metric": "probability",
                "passed": False,
                "blocks": ["live:cum=0.20<0.85", "live:no_recent_threat"],
            },
            "away_goal": {
                "probability": 0.50,
                "metric": "confidence",
                "passed": False,
                "blocks": ["side_pressure_low", "side_no_recent_threat"],
            },
        },
        "router": {
            "status": "WAIT",
            "winner": None,
            "alternatives": [],
            "rejected": [
                {
                    "key": "away_total:1.5",
                    "family": "team_total",
                    "strategy": "away_goal",
                    "label": "ИТБ2 1.5",
                    "odd": 12.40,
                    "rating": 66.0,
                    "expected_roi": 5.20,
                    "value_edge_pp": 41.9,
                    "blocks": ["gool_wait_without_verified_override"],
                },
                {
                    "key": "match_total:2.5",
                    "family": "match_total",
                    "strategy": "another_goal",
                    "label": "ТБ 2.5",
                    "odd": 1.82,
                    "rating": 55.0,
                    "blocks": ["gool_wait_without_verified_override"],
                },
            ],
        },
    }


def test_wait_preview_prefers_general_next_goal_total_and_plain_language(monkeypatch):
    monkeypatch.setattr(multi_analysis_view, "_latest_analysis", lambda: {"m1": _wait_row()})
    text = multi_analysis_view.analysis_text()

    assert "🎯 Ещё 1 гол: ТБ 2.5 @ 1.82" in text
    assert "ИТБ2 1.5 @ 12.40" not in text
    assert "Почему ждём: мало давления" in text
    assert "давно нет опасных атак" in text
    assert "EV " not in text
    assert "edge " not in text
    assert "C50" not in text
    assert "КРАТКИЙ ОТЧЁТ" in text


def test_xbet_score_unavailable_is_plain_bookmaker_reason_not_event_guard(monkeypatch):
    row = _wait_row(guard_reason="XBET_SCORE_UNAVAILABLE")
    monkeypatch.setattr(multi_analysis_view, "_latest_analysis", lambda: {"m1": row})
    text = multi_analysis_view.analysis_text()

    assert "1xBet не подтвердил текущий счёт" in text
    assert "EVENT GUARD" not in text
