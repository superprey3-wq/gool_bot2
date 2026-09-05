from __future__ import annotations

from datetime import datetime, timezone

import gool_bot2.multi_delivery as delivery
import gool_bot2.telegram as telegram
from gool_bot2.journal import save_signal_journal, load_signal_journal


def test_failed_result_attempt_gets_backoff(tmp_path, monkeypatch):
    path = tmp_path / "journal.json"
    row = {
        "entry_key": "e1", "match_id": "m1", "mode": "active", "telegram_sent": True,
        "result": "won", "result_notification_pending": True,
    }
    save_signal_journal(path, [row])
    assert len(delivery.pending_result_notifications(path, match_id="m1")) == 1
    assert delivery.finalize_result_delivery(path, row, 0) is False
    assert delivery.pending_result_notifications(path, match_id="m1") == []
    stored = load_signal_journal(path)[0]
    assert stored["result_notification_attempts"] == 1
    assert stored["result_notification_pending"] is True


def test_telegram_circuit_prevents_repeated_blocking_calls(monkeypatch):
    telegram._TELEGRAM_UNAVAILABLE_UNTIL = 0.0
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "1:test")
    monkeypatch.setenv("TELEGRAM_NETWORK_BACKOFF_SECONDS", "30")
    calls = {"n": 0}
    def broken(*args, **kwargs):
        calls["n"] += 1
        raise TimeoutError("network")
    monkeypatch.setattr(telegram, "urlopen", broken)
    assert telegram._api_call("sendMessage", {"chat_id": "1", "text": "x"}) is None
    assert calls["n"] == 1
    assert telegram._api_call("sendMessage", {"chat_id": "1", "text": "x"}) is None
    assert calls["n"] == 1
    telegram._TELEGRAM_UNAVAILABLE_UNTIL = 0.0
