from __future__ import annotations

from pathlib import Path

from gool_bot2 import storage_market_signal_worker_var as worker_var


def test_inline_and_main_telegram_polls_share_one_cursor(tmp_path: Path, monkeypatch):
    calls: list[int] = []

    def fake_poll(journal_path, offset: int = 0, timeout: int = 0):
        del journal_path, timeout
        calls.append(int(offset))
        return int(offset) + 5, 1

    monkeypatch.setattr(worker_var, "_ORIG_POLL", fake_poll)
    monkeypatch.setattr(worker_var, "_INLINE_TELEGRAM_OFFSET", 0)
    monkeypatch.setattr(worker_var, "_LAST_INLINE_TELEGRAM_POLL", 0.0)
    monkeypatch.setattr(worker_var, "daily_report_due_date", lambda path: None)
    monkeypatch.setenv("SIGNAL_JOURNAL", str(tmp_path / "signal_journal.json"))

    assert worker_var._poll_inline_telegram(force=True) == 1
    assert worker_var._INLINE_TELEGRAM_OFFSET == 5

    next_offset, actions = worker_var._poll_with_multi_bank(
        tmp_path / "signal_journal.json",
        offset=0,
        timeout=0,
    )

    assert calls == [0, 5]
    assert next_offset == 10
    assert actions == 1
    assert worker_var._INLINE_TELEGRAM_OFFSET == 10


def test_inline_telegram_poll_failure_never_breaks_match_processing(tmp_path: Path, monkeypatch):
    def broken_poll(*args, **kwargs):
        del args, kwargs
        raise RuntimeError("telegram unavailable")

    monkeypatch.setattr(worker_var, "_ORIG_POLL", broken_poll)
    monkeypatch.setattr(worker_var, "_INLINE_TELEGRAM_OFFSET", 0)
    monkeypatch.setattr(worker_var, "_LAST_INLINE_TELEGRAM_POLL", 0.0)
    monkeypatch.setenv("SIGNAL_JOURNAL", str(tmp_path / "signal_journal.json"))

    assert worker_var._poll_inline_telegram(force=True) == 0
    assert worker_var._INLINE_TELEGRAM_OFFSET == 0
