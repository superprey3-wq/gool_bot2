import json

from gool_bot2.multi_reentry_guard import enforce_reentry_cooldown
from gool_bot2.multi_router import MarketCandidate, RouterDecision


def _decision(minute: int, strategy: str = "another_goal") -> RouterDecision:
    winner = MarketCandidate(
        key="match_total:2.5",
        family="match_total",
        strategy=strategy,
        label="ТБ 2.5",
        odd=1.60,
        model_probability=0.80,
        rating=80.0,
    )
    return RouterDecision(
        status="BET",
        minute=minute,
        score=(1, 1),
        winner=winner,
        alternatives=[],
        rejected=[],
        reason="test",
    )


def _record(minute: int) -> dict:
    return {
        "match": {
            "flashscore_event_id": "match-1",
            "minute": minute,
        }
    }


def _write_journal(path, rows) -> None:
    path.write_text(json.dumps(rows), encoding="utf-8")


def test_first_half_settlement_does_not_block_second_half_at_46(tmp_path) -> None:
    journal = tmp_path / "journal.json"
    _write_journal(journal, [{
        "match_id": "match-1",
        "strategy": "goal_before_ht",
        "result": "won",
        "settled_minute": 45,
    }])

    decision = enforce_reentry_cooldown(_decision(46), _record(46), journal)

    assert decision.status == "BET"
    assert decision.winner is not None
    assert decision.winner.strategy == "another_goal"


def test_same_second_half_system_keeps_ten_minute_cooldown(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("GOOL_MULTI_REENTRY_COOLDOWN_MINUTES", "10")
    journal = tmp_path / "journal.json"
    _write_journal(journal, [{
        "match_id": "match-1",
        "strategy": "another_goal",
        "result": "won",
        "settled_minute": 50,
    }])

    blocked = enforce_reentry_cooldown(_decision(55), _record(55), journal)
    assert blocked.status == "WAIT"
    assert blocked.winner is None
    assert "reentry_cooldown_10m" in blocked.rejected[0].blocks

    allowed = enforce_reentry_cooldown(_decision(60), _record(60), journal)
    assert allowed.status == "BET"
    assert allowed.winner is not None


def test_steam_has_separate_cooldown_bucket(tmp_path) -> None:
    journal = tmp_path / "journal.json"
    _write_journal(journal, [{
        "match_id": "match-1",
        "strategy": "steam_goal_before_ht",
        "result": "lost",
        "settled_minute": 45,
    }])

    ordinary = enforce_reentry_cooldown(_decision(46, "another_goal"), _record(46), journal)
    assert ordinary.status == "BET"
    assert ordinary.winner is not None
