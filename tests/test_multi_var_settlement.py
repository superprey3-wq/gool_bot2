from __future__ import annotations

from pathlib import Path

from gool_bot2.journal import load_signal_journal, save_signal_journal
from gool_bot2.multi_journal import record_multi_entry, settle_multi_journal
from gool_bot2.multi_router import MarketCandidate, RouterDecision
from gool_bot2.providers.flashscore_incident_guard import goals_from_incidents, parse_summary_incidents
import gool_bot2.var_settlement_guard as var_guard


def _candidate(key: str, family: str, strategy: str, label: str, *, odd: float = 1.97) -> MarketCandidate:
    return MarketCandidate(
        key=key,
        family=family,
        strategy=strategy,
        label=label,
        odd=odd,
        model_probability=0.83,
        rating=77.0,
        expected_roi=0.10,
        value_edge_pp=8.0,
        market_pressure_pp=4.0,
    )


def _decision(candidate: MarketCandidate, minute: int, score: tuple[int, int]) -> RouterDecision:
    return RouterDecision(
        status="BET",
        minute=minute,
        score=score,
        winner=candidate,
        alternatives=[],
        rejected=[],
        reason="test",
    )


def _record(
    minute: int,
    score: tuple[int, int],
    *,
    status: str = "",
    halftime: bool = False,
    finished: bool = False,
    timeline: list[dict] | None = None,
):
    return {
        "match": {
            "flashscore_event_id": "wuhan-qingdao",
            "home": "Wuhan Three Towns",
            "away": "Qingdao West Coast",
            "league": "China: Super League",
            "minute": minute,
            "home_score": score[0],
            "away_score": score[1],
            "is_halftime": halftime,
            "is_finished": finished,
            "status_code": status,
        },
        "providers": {
            "flashscore": {
                "meta": {
                    "goal_timeline": list(timeline or []),
                    "endpoints": {"master": {"status_code": status, "score": list(score)}},
                }
            }
        },
    }


def test_flashscore_disallowed_goal_is_removed_from_goal_timeline() -> None:
    body = (
        "III÷g1¬IA÷2¬IB÷3'¬IE÷3¬INX÷0¬IOX÷1¬IK÷Goal"
        "~III÷g2¬IA÷1¬IB÷19'¬IE÷3¬INX÷1¬IOX÷1¬IK÷Goal"
        "~III÷g3¬IA÷1¬IB÷22'¬IE÷3¬INX÷2¬IOX÷1¬IK÷Goal"
        "~III÷var¬IA÷2¬IB÷43'¬IE÷3¬INX÷2¬IOX÷2¬IK÷Goal"
        "¬IE÷11¬IK÷Goal Disallowed - Offside"
    )

    incidents = parse_summary_incidents(body)
    goals = goals_from_incidents(incidents)

    assert [row["score"] for row in goals] == [[0, 1], [1, 1], [2, 1]]
    assert all(row["minute"] != 43 for row in goals)
    disallowed = [row for row in incidents if row["event_type"] == "goal_disallowed"]
    assert len(disallowed) == 1
    assert disallowed[0]["minute"] == 43
    assert disallowed[0]["side"] == "away"


def test_first_half_total_never_settles_on_live_provisional_goal(tmp_path: Path) -> None:
    path = tmp_path / "multi.json"
    bet = _candidate("first_half_total:3.5", "first_half_total", "goal_before_ht", "1Т ТБ 3.5")
    assert record_multi_entry(
        _record(29, (2, 1), status="12"),
        _decision(bet, 29, (2, 1)),
        {},
        path,
        data_quality=0.9,
    )

    changed = settle_multi_journal(
        _record(
            43,
            (2, 2),
            status="12",
            timeline=[
                {"minute": 3, "score": [0, 1], "period": "1H", "event_type": "goal"},
                {"minute": 19, "score": [1, 1], "period": "1H", "event_type": "goal"},
                {"minute": 22, "score": [2, 1], "period": "1H", "event_type": "goal"},
                {"minute": 43, "score": [2, 2], "period": "1H", "event_type": "goal"},
            ],
        ),
        path,
    )

    assert changed == []
    assert load_signal_journal(path)[0]["result"] == "pending"


def test_first_half_total_uses_authoritative_halftime_score_after_var_rollback(tmp_path: Path) -> None:
    path = tmp_path / "multi.json"
    bet = _candidate("first_half_total:3.5", "first_half_total", "goal_before_ht", "1Т ТБ 3.5")
    assert record_multi_entry(
        _record(29, (2, 1), status="12"),
        _decision(bet, 29, (2, 1)),
        {},
        path,
        data_quality=0.9,
    )

    changed = settle_multi_journal(
        _record(
            45,
            (2, 1),
            status="38",
            halftime=True,
            # Deliberately stale timeline still contains the cancelled 2:2.
            timeline=[
                {"minute": 3, "score": [0, 1], "period": "1H", "event_type": "goal"},
                {"minute": 19, "score": [1, 1], "period": "1H", "event_type": "goal"},
                {"minute": 22, "score": [2, 1], "period": "1H", "event_type": "goal"},
                {"minute": 43, "score": [2, 2], "period": "1H", "event_type": "goal"},
            ],
        ),
        path,
    )

    assert len(changed) == 1
    row = load_signal_journal(path)[0]
    assert row["result"] == "lost"
    assert row["settled_score"] == [2, 1]
    assert row["settled_minute"] == 45
    assert row["settlement_source"] == "flashscore_halftime_master"
    assert row["profit_units"] == -1.0


def test_wrong_first_half_win_is_auto_corrected_and_bank_profit_reversed(tmp_path: Path) -> None:
    path = tmp_path / "multi.json"
    bet = _candidate("first_half_total:3.5", "first_half_total", "goal_before_ht", "1Т ТБ 3.5")
    assert record_multi_entry(
        _record(29, (2, 1), status="12"),
        _decision(bet, 29, (2, 1)),
        {},
        path,
        data_quality=0.9,
    )

    rows = load_signal_journal(path)
    rows[0].update(
        {
            "result": "won",
            "profit_units": 0.97,
            "settled_minute": 43,
            "settled_score": [2, 2],
            "settled_at": "2026-09-05T11:43:00+00:00",
            "settlement_source": "flashscore_first_half_timeline",
            "result_notification_pending": False,
            "result_telegram_sent": True,
        }
    )
    save_signal_journal(path, rows)

    changed = settle_multi_journal(
        _record(45, (2, 1), status="38", halftime=True),
        path,
    )

    assert len(changed) == 1
    row = load_signal_journal(path)[0]
    assert row["result"] == "lost"
    assert row["profit_units"] == -1.0
    assert row["settled_score"] == [2, 1]
    assert row["settlement_corrected"] is True
    assert row["settlement_correction"]["result"] == "won"
    assert row["result_notification_pending"] is True
    assert row["virtual_profit_rub"] == -float(row["virtual_stake_rub"])


def test_another_goal_requires_stable_score_before_live_win(tmp_path: Path, monkeypatch) -> None:
    path = tmp_path / "multi.json"
    bet = _candidate("match_total:3.5", "match_total", "another_goal", "ТБ 3.5", odd=1.80)
    assert record_multi_entry(
        _record(60, (2, 1), status="13"),
        _decision(bet, 60, (2, 1)),
        {},
        path,
        data_quality=0.9,
    )

    monkeypatch.setenv("VAR_WIN_CONFIRM_SECONDS", "12")
    monkeypatch.setenv("VAR_WIN_CONFIRM_SNAPSHOTS", "2")

    now = {"value": 100.0}
    monkeypatch.setattr(var_guard.time, "time", lambda: now["value"])

    # Provisional 2:2: do not settle immediately.
    assert settle_multi_journal(_record(61, (2, 2), status="13"), path) == []
    assert load_signal_journal(path)[0]["result"] == "pending"

    # VAR rollback clears confirmation state.
    now["value"] = 110.0
    assert settle_multi_journal(_record(61, (2, 1), status="13"), path) == []
    assert load_signal_journal(path)[0]["result"] == "pending"

    # A later real goal must survive both the time and snapshot thresholds.
    now["value"] = 200.0
    assert settle_multi_journal(_record(64, (2, 2), status="13"), path) == []
    now["value"] = 213.0
    changed = settle_multi_journal(_record(64, (2, 2), status="13"), path)

    assert len(changed) == 1
    row = load_signal_journal(path)[0]
    assert row["result"] == "won"
    assert row["settled_score"] == [2, 2]
    assert row["settlement_source"] == "flashscore_var_confirmed_live_score"
