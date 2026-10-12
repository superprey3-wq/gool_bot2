from __future__ import annotations

from datetime import date, datetime, timedelta
from pathlib import Path

import pytest

from gool_bot2.report_date_request import requested_report_date


def test_report_date_accepts_iso_russian_and_bot_mentions():
    day = date(2026, 10, 12)
    assert requested_report_date("/dayreport 2026-10-12") == day
    assert requested_report_date("/fullreport 12.10.2026") == day
    assert requested_report_date("/dayreport@GOOL_TEST 2026-10-12") == day
    assert requested_report_date("/dayreport") is None
    assert requested_report_date("/fullreport") is None


def test_report_date_rejects_invalid_or_future():
    with pytest.raises(ValueError):
        requested_report_date("/dayreport 2026-99-12")
    with pytest.raises(ValueError):
        requested_report_date("/dayreport 2026-10-12 anything")
    today = datetime.now(__import__("gool_bot2.full_report_export", fromlist=["_tz"])._tz()).date()
    with pytest.raises(ValueError):
        requested_report_date(f"/dayreport {(today + timedelta(days=1)).isoformat()}")
    assert requested_report_date("/dayreport вчера") == today - timedelta(days=1)


def test_direct_background_responder_returns_historical_html(tmp_path: Path, monkeypatch):
    from gool_bot2 import storage_market_signal_worker_var as worker
    from gool_bot2 import full_report_export

    selected = []
    documents = []
    monkeypatch.setattr(full_report_export, "build_full_report", lambda **kwargs: (
        selected.append(kwargs.get("report_date")) or "GOOL_DAY_REPORT_2026-10-12.html",
        b"<html>12 October</html>", "12 October",
    ))
    monkeypatch.setattr(worker.telegram_mod, "send_document", lambda *args, **kwargs: documents.append((args, kwargs)) or True)
    assert worker._handle_direct_telegram_update(
        "test-token", tmp_path / "j.json",
        {"update_id": 10, "message": {"chat": {"id": 123}, "text": "/dayreport 2026-10-12"}},
    ) == 1
    assert selected == [date(2026, 10, 12)]
    assert documents[0][0][1] == "GOOL_DAY_REPORT_2026-10-12.html"


def test_standard_telegram_responder_returns_historical_html(tmp_path: Path, monkeypatch):
    from gool_bot2 import telegram, full_report_export
    selected = []
    sent = []
    monkeypatch.setattr(telegram, "_drain_prematch_result_notifications", lambda *_: None, raising=False)
    monkeypatch.setattr(telegram, "_api_call", lambda *args, **kwargs: {
        "ok": True, "result": [{"update_id": 18, "message": {
            "chat": {"id": 123}, "text": "/dayreport 12.10.2026"
        }}],
    })
    monkeypatch.setattr(full_report_export, "build_full_report", lambda **kwargs: (
        selected.append(kwargs.get("report_date")) or "GOOL_DAY_REPORT_2026-10-12.html",
        b"<html>12 October</html>", "12 October",
    ))
    monkeypatch.setattr(telegram, "send_document", lambda *args, **kwargs: sent.append(args) or True)
    assert telegram.poll_telegram_updates(tmp_path / "j.json", offset=18)[1] == 1
    assert selected == [date(2026, 10, 12)]
    assert sent[0][1] == "GOOL_DAY_REPORT_2026-10-12.html"
