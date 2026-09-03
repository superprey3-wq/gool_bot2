from gool_bot2 import storage_market_signal_worker_var as mod


def test_var_guard_uses_core_win_predicate(monkeypatch):
    record = {
        "match": {
            "flashscore_event_id": "runtime-var-test",
            "minute": 60,
            "home_score": 1,
            "away_score": 0,
            "is_finished": False,
        }
    }
    row = {
        "match_id": "runtime-var-test",
        "head": "another_goal",
        "minute": 50,
        "score": [0, 0],
        "result": "won",
        "created_at": "2026-09-03T00:00:00+00:00",
    }
    journal = [row]

    monkeypatch.setattr(mod, "_ORIG_SETTLE", lambda _record, _journal: [dict(row)])
    monkeypatch.setattr(mod.base, "_reconciled_score", lambda _record: (1, 0))
    monkeypatch.setattr(mod, "confirmed_win", lambda *_args, **_kwargs: True)

    settled = mod._guard_main(record, journal)

    assert len(settled) == 1
    assert settled[0]["head"] == "another_goal"
