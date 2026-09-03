from __future__ import annotations

from pathlib import Path

from gool_bot2.journal import load_signal_journal
from gool_bot2.multi_journal import record_multi_entry, settle_multi_journal
from gool_bot2.multi_router import MarketCandidate, RouterDecision


def _decision(candidate: MarketCandidate) -> RouterDecision:
    return RouterDecision(
        status="BET",
        minute=35,
        score=(0, 0),
        winner=candidate,
        alternatives=[],
        rejected=[],
        reason="best",
    )


def _candidate(key: str, family: str, strategy: str, label: str) -> MarketCandidate:
    return MarketCandidate(
        key=key,
        family=family,
        strategy=strategy,
        label=label,
        odd=2.0,
        model_probability=0.60,
        rating=75.0,
        expected_roi=0.20,
        value_edge_pp=10.0,
        market_pressure_pp=4.0,
    )


def _record(minute: int, score: tuple[int, int], *, finished: bool = False, halftime: bool = False, timeline=None):
    return {
        "match": {
            "flashscore_event_id": "abc12345",
            "home": "Home",
            "away": "Away",
            "league": "Test",
            "minute": minute,
            "home_score": score[0],
            "away_score": score[1],
            "is_finished": finished,
            "is_halftime": halftime,
        },
        "providers": {"flashscore": {"meta": {"goal_timeline": list(timeline or [])}}},
    }


def test_one_open_multi_exposure_and_score_epoch_dedupe(tmp_path: Path):
    path = tmp_path / "multi.json"
    first = _candidate("match_total:0.5", "match_total", "another_goal", "ТБ 0.5")
    second = _candidate("home_total:0.5", "team_total", "home_goal", "ИТБ1 0.5")

    created = record_multi_entry(_record(35, (0, 0)), _decision(first), {}, path, data_quality=0.8)
    assert created is not None
    assert record_multi_entry(_record(35, (0, 0)), _decision(second), {}, path, data_quality=0.8) is None
    assert len(load_signal_journal(path)) == 1

    settle_multi_journal(
        _record(40, (1, 0), timeline=[{"minute": 40, "score": [1, 0], "side": "home"}]),
        path,
    )
    rows = load_signal_journal(path)
    assert rows[0]["result"] == "won"
    assert rows[0]["profit_units"] == 1.0

    # A new score epoch may produce a new BEST BET after the previous exposure settled.
    later = _candidate("match_total:1.5", "match_total", "another_goal", "ТБ 1.5")
    created2 = record_multi_entry(_record(44, (1, 0)), _decision(later), {}, path, data_quality=0.8)
    assert created2 is not None
    assert len(load_signal_journal(path)) == 2


def test_first_half_market_does_not_use_second_half_goal(tmp_path: Path):
    path = tmp_path / "multi.json"
    fh = _candidate("first_half_total:0.5", "first_half_total", "goal_before_ht", "1Т ТБ 0.5")
    assert record_multi_entry(_record(35, (0, 0)), _decision(fh), {}, path, data_quality=0.9)

    changed = settle_multi_journal(
        _record(
            60,
            (1, 0),
            timeline=[{"minute": 50, "score": [1, 0], "side": "home"}],
        ),
        path,
    )
    assert len(changed) == 1
    row = load_signal_journal(path)[0]
    assert row["result"] == "lost"
    assert row["settled_minute"] == 45
    assert row["settled_score"] == [0, 0]
    assert row["profit_units"] == -1.0


def test_first_half_market_wins_only_from_first_half_timeline(tmp_path: Path):
    path = tmp_path / "multi.json"
    fh = _candidate("first_half_total:0.5", "first_half_total", "goal_before_ht", "1Т ТБ 0.5")
    assert record_multi_entry(_record(30, (0, 0)), _decision(fh), {}, path, data_quality=0.9)

    settle_multi_journal(
        _record(
            45,
            (1, 0),
            halftime=True,
            timeline=[{"minute": 42, "score": [1, 0], "side": "home"}],
        ),
        path,
    )
    row = load_signal_journal(path)[0]
    assert row["result"] == "won"
    assert row["settled_minute"] == 42
    assert row["settled_score"] == [1, 0]


def test_first_half_unchanged_score_after_break_is_safe_loss_without_timeline(tmp_path: Path):
    path = tmp_path / "multi.json"
    fh = _candidate("first_half_total:0.5", "first_half_total", "goal_before_ht", "1Т ТБ 0.5")
    assert record_multi_entry(_record(35, (0, 0)), _decision(fh), {}, path, data_quality=0.9)

    settle_multi_journal(_record(55, (0, 0)), path)
    row = load_signal_journal(path)[0]
    assert row["result"] == "lost"
    assert row["settlement_source"] == "no_first_half_goal_score_unchanged"
    assert row["profit_units"] == -1.0


def test_first_half_changed_score_without_timeline_is_void_not_guessed(tmp_path: Path):
    path = tmp_path / "multi.json"
    fh = _candidate("first_half_total:0.5", "first_half_total", "goal_before_ht", "1Т ТБ 0.5")
    assert record_multi_entry(_record(35, (0, 0)), _decision(fh), {}, path, data_quality=0.9)

    settle_multi_journal(_record(55, (1, 0)), path)
    row = load_signal_journal(path)[0]
    assert row["result"] == "void"
    assert row["settlement_source"] == "half_time_score_unavailable"
    assert row["profit_units"] == 0.0
