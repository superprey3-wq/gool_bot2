"""Offline Telegram integration checks for the three-sport lightweight responder."""
from __future__ import annotations

import json
from pathlib import Path

import bot_hosting_lite


def test_lite_commands_and_dayreport_work_offline(tmp_path, monkeypatch):
    from gool_bot2 import telegram
    monkeypatch.setenv("RUNTIME_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("GOOL_FOOTBALL_AUTOINSTALL", "0")
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "12345:offline_test_only")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "1003")
    monkeypatch.setenv("GOOL_MULTISPORT_MODE", "shadow")
    state = tmp_path / "live"
    state.mkdir()
    (state / "gool_multisport_state.json").write_text(json.dumps({
        "captured_at": "2026-10-19T22:00:00Z",
        "sports": {"hockey": {"enabled": True}, "basketball": {"enabled": True}}
    }), encoding="utf8")
    (state / "gool_lite_football_state.json").write_text(json.dumps({
        "captured_at": "2026-10-19T22:01:00Z",
        "prematch": {"analysed": 2, "scenario_matches": 1, "priced": 0},
        "live": {"live_total": 3, "analysed": 1, "decisions": []}
    }), encoding="utf8")
    commands = ("/start", "/status", "/football", "/footballlive", "/footballprematch",
                "/super10", "/parlays", "/dayreport", "/hockeyjournal", "/basketjournal")
    updates = [{
        "update_id": idx + 1,
        "message": {"chat": {"id": 1003}, "text": cmd}
    } for idx, cmd in enumerate(commands)]
    monkeypatch.setattr(telegram, "_api_call", lambda *args, **kwargs: {"ok": True, "result": updates})
    messages = []
    documents = []

    def send_message(cid, content, **_):
        messages.append((cid, str(content)))
        return True

    def send_document(cid, filename, data, **kwargs):
        documents.append((cid, filename, data, kwargs))
        return True

    monkeypatch.setattr(telegram, "send_message", send_message)
    monkeypatch.setattr(telegram, "send_document", send_document)

    poll = bot_hosting_lite._telegram_loop(state, interval=0)
    poll()
    assert len(messages) == len(commands) - 1
    assert len(documents) == 1
    assert "Футбол" in messages[1][1]
    assert "PREMATCH" in messages[2][1]
    assert documents[0][1].endswith(".html")
    assert documents[0][2].startswith(b"<!DOCTYPE html") or b"<html" in documents[0][2][:500]
    assert documents[0][3]["content_type"] == "text/html"
    assert (state / "telegram_offset.txt").read_text() == str(len(commands) + 1)
