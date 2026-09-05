from pathlib import Path

from gool_bot2 import storage_market_signal_worker_var as worker


def test_background_analysis_reply_uses_captured_credential(tmp_path: Path, monkeypatch):
    sent = []
    monkeypatch.setattr(worker.telegram_mod, "analysis_text", lambda *_args, **_kwargs: "ANALYSIS_OK")
    monkeypatch.setattr(
        worker,
        "_direct_send_message",
        lambda credential, chat_id, text, reply_markup=None: sent.append((credential, chat_id, text)) or True,
    )

    actions = worker._handle_direct_telegram_update(
        "test-credential",
        tmp_path / "signal_journal.json",
        {"update_id": 10, "message": {"chat": {"id": 123}, "text": "🧠 Анализ"}},
    )

    assert actions == 1
    assert sent == [("test-credential", 123, "ANALYSIS_OK")]


def test_main_poll_does_not_compete_with_background_responder(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(worker, "_background_responder_alive", lambda: True)
    monkeypatch.setattr(worker, "daily_report_due_date", lambda _path: None)
    worker._DIRECT_TELEGRAM_OFFSET = 50
    worker._INLINE_TELEGRAM_OFFSET = 50

    next_offset, actions = worker._poll_with_multi_bank(tmp_path / "signal_journal.json", offset=12, timeout=0)

    assert next_offset == 50
    assert actions == 0
