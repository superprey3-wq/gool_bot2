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



def test_super10_is_on_main_keyboard_and_has_inline_actions():
    from gool_bot2.bot_menu import MENU_KEYBOARD
    from gool_bot2.telegram import super10_keyboard

    texts = [
        button["text"]
        for row in MENU_KEYBOARD["keyboard"]
        for button in row
    ]
    assert "🌐 SUPER 10" in texts
    assert "📄 Отчёт за день" not in texts
    assert "📊 Отчёт" in texts

    inline = super10_keyboard()["inline_keyboard"][0]
    assert {button["callback_data"] for button in inline} == {"s10:refresh", "s10:history"}



def test_send_document_uses_telegram_multipart(monkeypatch):
    from gool_bot2 import telegram

    calls = []
    monkeypatch.setattr(
        telegram,
        "_multipart_call",
        lambda method, fields, file_field, filename, file_bytes, content_type="image/png", timeout=25, token_override=None: calls.append(
            (method, fields, file_field, filename, file_bytes, content_type, token_override)
        ) or {"ok": True},
    )

    assert telegram.send_document(
        123,
        "report.html",
        b"<html/>",
        caption="REPORT",
        content_type="text/html; charset=utf-8",
        token_override="secret-token",
    ) is True
    assert calls[0][0] == "sendDocument"
    assert calls[0][2] == "document"
    assert calls[0][3] == "report.html"
    assert calls[0][4] == b"<html/>"
    assert calls[0][5] == "text/html; charset=utf-8"
    assert calls[0][6] == "secret-token"
