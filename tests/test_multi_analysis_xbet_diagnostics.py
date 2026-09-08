from __future__ import annotations

import gool_bot2.multi_analysis_view as view


def _row(*, minute: int = 60, state: str = "HARD_NO", market: bool = False) -> dict:
    return {
        "match_id": "m1",
        "home": "Home",
        "away": "Away",
        "minute": minute,
        "score": [0, 0],
        "market": (
            {"available": True, "targets": {}}
            if market
            else {
                "available": False,
                "reason": "xbet_match_not_mapped_or_state_missing",
                "targets": {},
            }
        ),
        "experts": {
            "another_goal": {
                "state": state,
                "passed": state == "PASS",
                "probability": 0.67 if state == "BORDERLINE" else 0.20,
                "blocks": [
                    "expert_borderline" if state == "BORDERLINE" else "expert_hard_no"
                ],
            },
            "goal_before_ht": {
                "state": state,
                "passed": state == "PASS",
                "probability": 0.67 if state == "BORDERLINE" else 0.20,
                "blocks": [
                    "expert_borderline" if state == "BORDERLINE" else "expert_hard_no"
                ],
            },
        },
        "router": {"status": "WAIT", "winner": None, "rejected": []},
        "context": {"live": {}},
    }


def test_wait_reason_does_not_blame_only_xbet_when_football_is_hard_no():
    reason = view._wait_reason(_row(state="HARD_NO", market=False), None)
    assert "GOOL не подтверждает сценарий" in reason
    assert "1xBet матч не сопоставлен" in reason


def test_borderline_match_keeps_xbet_missing_visible_as_second_blocker():
    reason = view._wait_reason(_row(state="BORDERLINE", market=False), None)
    assert "GOOL ещё не набрал порог подтверждения" in reason
    assert "1xBet матч не сопоставлен" in reason


def test_xbet_coverage_reports_all_matches_and_actionable_subset():
    states = [
        _row(state="HARD_NO", market=False),
        _row(state="BORDERLINE", market=False),
        _row(state="PASS", market=True),
        _row(minute=80, state="PASS", market=True),
    ]
    line = view._xbet_coverage_line(states)
    assert "2/4" in line
    assert "1/2" in line


def test_short_report_includes_xbet_coverage(monkeypatch):
    rows = {
        "a": _row(state="BORDERLINE", market=False),
        "b": {**_row(state="PASS", market=True), "match_id": "m2", "home": "A", "away": "B"},
    }
    monkeypatch.setattr(view, "_latest_analysis", lambda: rows)
    text = view.analysis_text()
    assert "📡 1xBet" in text
    assert "1/2" in text
    assert "PASS/BORDERLINE" in text
