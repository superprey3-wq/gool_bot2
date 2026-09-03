from __future__ import annotations

from datetime import date, datetime, timezone
from pathlib import Path

from gool_bot2.multi_bank import (
    bank_at,
    daily_report_due_date,
    ensure_bank_fields,
    ensure_state,
    mark_daily_report_sent,
    render_daily_bank_report,
)


def _row(created: str, *, result: str = "pending", settled: str | None = None, odd: float = 2.0):
    row = {
        "created_at": created,
        "match_id": created,
        "odd": odd,
        "result": result,
    }
    if settled:
        row["settled_at"] = settled
    if result == "won":
        row["profit_units"] = odd - 1.0
    elif result == "lost":
        row["profit_units"] = -1.0
    elif result in {"void", "push"}:
        row["profit_units"] = 0.0
    return row


def test_virtual_bank_compounds_only_after_realized_settlement(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("GOOL_MULTI_BANK_INITIAL_RUB", "100000")
    monkeypatch.setenv("GOOL_MULTI_BANK_STAKE_PCT", "0.02")
    path = tmp_path / "multi.json"
    ensure_state(path, started_at=datetime(2026, 9, 3, tzinfo=timezone.utc))

    rows = [
        _row("2026-09-03T10:00:00+00:00", result="won", settled="2026-09-03T10:30:00+00:00"),
        _row("2026-09-03T11:00:00+00:00"),
    ]
    assert ensure_bank_fields(rows, path)
    assert rows[0]["virtual_bank_before_rub"] == 100000.0
    assert rows[0]["virtual_stake_rub"] == 2000.0
    assert rows[0]["virtual_profit_rub"] == 2000.0
    assert rows[1]["virtual_bank_before_rub"] == 102000.0
    assert rows[1]["virtual_stake_rub"] == 2040.0
    state = ensure_state(path)
    assert bank_at(rows, state, datetime(2026, 9, 3, 12, tzinfo=timezone.utc)) == 102000.0


def test_two_open_bets_use_same_realized_bank_until_one_settles(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("GOOL_MULTI_BANK_INITIAL_RUB", "100000")
    monkeypatch.setenv("GOOL_MULTI_BANK_STAKE_PCT", "0.02")
    path = tmp_path / "multi.json"
    ensure_state(path, started_at=datetime(2026, 9, 3, tzinfo=timezone.utc))
    rows = [
        _row("2026-09-03T10:00:00+00:00"),
        _row("2026-09-03T10:10:00+00:00"),
    ]
    ensure_bank_fields(rows, path)
    assert rows[0]["virtual_stake_rub"] == 2000.0
    assert rows[1]["virtual_stake_rub"] == 2000.0


def test_daily_report_tracks_ruble_bank_and_month(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("GOOL_MULTI_BANK_INITIAL_RUB", "100000")
    monkeypatch.setenv("GOOL_MULTI_BANK_STAKE_PCT", "0.02")
    monkeypatch.setenv("REPORT_TIMEZONE", "UTC")
    path = tmp_path / "multi.json"
    ensure_state(path, started_at=datetime(2026, 9, 3, tzinfo=timezone.utc))
    rows = [
        _row("2026-09-03T10:00:00+00:00", result="won", settled="2026-09-03T10:30:00+00:00"),
        _row("2026-09-03T11:00:00+00:00", result="lost", settled="2026-09-03T12:00:00+00:00"),
    ]
    ensure_bank_fields(rows, path)
    from gool_bot2.journal import save_signal_journal

    save_signal_journal(path, rows)
    report = render_daily_bank_report(
        path,
        report_date=date(2026, 9, 3),
        now=datetime(2026, 9, 3, 23, 59, 30, tzinfo=timezone.utc),
    )
    assert "Банк 00:00: <b>100 000 ₽</b>" in report
    assert "Ставок открыто: <b>2</b>" in report
    assert "✅ 1 · ❌ 1" in report
    assert "P/L дня: <b>−40 ₽</b>" in report
    assert "Банк 23:59: <b>99 960 ₽</b>" in report
    assert "P/L месяца: <b>−40 ₽</b>" in report


def test_daily_report_becomes_due_at_2359_and_only_once(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("REPORT_TIMEZONE", "UTC")
    monkeypatch.setenv("GOOL_MULTI_BANK_REPORT_HOUR", "23")
    monkeypatch.setenv("GOOL_MULTI_BANK_REPORT_MINUTE", "59")
    path = tmp_path / "multi.json"
    ensure_state(path, started_at=datetime(2026, 9, 3, 10, tzinfo=timezone.utc))

    assert daily_report_due_date(path, now=datetime(2026, 9, 3, 23, 58, 59, tzinfo=timezone.utc)) is None
    due = daily_report_due_date(path, now=datetime(2026, 9, 3, 23, 59, 0, tzinfo=timezone.utc))
    assert due == date(2026, 9, 3)
    mark_daily_report_sent(path, due, sent_at=datetime(2026, 9, 3, 23, 59, 1, tzinfo=timezone.utc))
    assert daily_report_due_date(path, now=datetime(2026, 9, 3, 23, 59, 30, tzinfo=timezone.utc)) is None


def test_daily_report_catches_up_yesterday_after_restart(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("REPORT_TIMEZONE", "UTC")
    path = tmp_path / "multi.json"
    ensure_state(path, started_at=datetime(2026, 9, 3, 10, tzinfo=timezone.utc))
    assert daily_report_due_date(path, now=datetime(2026, 9, 4, 8, 0, 0, tzinfo=timezone.utc)) == date(2026, 9, 3)
