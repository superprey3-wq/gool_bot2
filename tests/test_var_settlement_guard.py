from __future__ import annotations

from gool_bot2.var_settlement_guard import clear_provisional, confirmed_win


def _row():
    return {
        "match_id": "m1",
        "head": "another_goal",
        "created_at": "2026-09-03T00:00:00+00:00",
        "minute": 60,
        "score": [0, 0],
        "result": "pending",
    }


def test_var_cancelled_goal_never_confirms(monkeypatch):
    monkeypatch.setenv("VAR_WIN_CONFIRM_SECONDS", "12")
    monkeypatch.setenv("VAR_WIN_CONFIRM_SNAPSHOTS", "2")
    row = _row()
    clear_provisional(row)

    assert confirmed_win(row, raw_won=True, minute=61, home_score=1, away_score=0, now=100.0) is False
    assert row.get("provisional_win") is True

    # VAR cancels the goal and the score returns to the signal score.
    assert confirmed_win(row, raw_won=False, minute=62, home_score=0, away_score=0, now=110.0) is False
    assert row.get("provisional_win") is None

    # A later fresh goal starts a new confirmation epoch rather than inheriting
    # the cancelled goal's first observation.
    assert confirmed_win(row, raw_won=True, minute=65, home_score=1, away_score=0, now=120.0) is False
    assert confirmed_win(row, raw_won=True, minute=65, home_score=1, away_score=0, now=125.0) is False


def test_stable_goal_confirms_only_after_time_and_snapshots(monkeypatch):
    monkeypatch.setenv("VAR_WIN_CONFIRM_SECONDS", "12")
    monkeypatch.setenv("VAR_WIN_CONFIRM_SNAPSHOTS", "2")
    row = _row()
    row["created_at"] = "2026-09-03T00:01:00+00:00"
    clear_provisional(row)

    assert confirmed_win(row, raw_won=True, minute=61, home_score=1, away_score=0, now=200.0) is False
    # Two snapshots alone are not enough if VAR confirmation time has not elapsed.
    assert confirmed_win(row, raw_won=True, minute=61, home_score=1, away_score=0, now=205.0) is False
    assert confirmed_win(row, raw_won=True, minute=62, home_score=1, away_score=0, now=213.0) is True
    assert row.get("provisional_win") is None
