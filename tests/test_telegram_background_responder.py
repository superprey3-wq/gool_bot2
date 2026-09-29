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


def test_production_start_uses_expanded_six_button_keyboard(tmp_path: Path, monkeypatch):
    sent = []
    monkeypatch.setattr(worker.telegram_mod, "subscribe", lambda _chat_id: True)
    monkeypatch.setattr(
        worker,
        "_direct_send_message",
        lambda credential, chat_id, text, reply_markup=None: sent.append(reply_markup) or True,
    )
    actions = worker._handle_direct_telegram_update(
        "test-credential",
        tmp_path / "signal_journal.json",
        {"update_id": 11, "message": {"chat": {"id": 123}, "text": "/start"}},
    )
    assert actions == 1
    labels = [button["text"] for row in sent[0]["keyboard"] for button in row]
    assert labels == ["📊 Отчёт", "🟢 В игре", "🎟 Ординары", "🔗 Экспрессы", "🧠 Анализ", "🔎 Найти матч"]


def test_production_prematch_menu_buttons_are_handled(tmp_path: Path, monkeypatch):
    sent = []
    monkeypatch.setattr(worker.telegram_mod, "prematch_singles_sections", lambda _path: ["SINGLES"])
    monkeypatch.setattr(worker.telegram_mod, "prematch_parlays_sections", lambda _path: ["PARLAYS"])
    monkeypatch.setattr(
        worker,
        "_direct_send_message",
        lambda credential, chat_id, text, reply_markup=None: sent.append(text) or True,
    )
    journal = tmp_path / "signal_journal.json"
    a = worker._handle_direct_telegram_update(
        "test-credential", journal,
        {"update_id": 12, "message": {"chat": {"id": 123}, "text": "🎟 Ординары"}},
    )
    b = worker._handle_direct_telegram_update(
        "test-credential", journal,
        {"update_id": 13, "message": {"chat": {"id": 123}, "text": "🔗 Экспрессы"}},
    )
    assert (a, b) == (1, 1)
    assert sent == ["SINGLES", "PARLAYS"]


def test_prematch_menu_hides_settled_and_started_rows(tmp_path):
    import json, time
    from datetime import datetime, timezone
    from gool_bot2.bot_menu import prematch_singles_sections
    now = time.time()
    created = datetime.now(timezone.utc).isoformat()
    rows = [
        {"origin":"prematch","result":"lost","home":"Old Lost","away":"X","created_at":created,"kickoff_ts":now+3600,"odd":1.8},
        {"origin":"prematch","result":"pending","home":"Already Started","away":"Y","created_at":created,"kickoff_ts":now-60,"odd":1.7},
        {"origin":"prematch","result":"pending","home":"Upcoming","away":"Z","created_at":created,"kickoff_ts":now+3600,"odd":1.6},
    ]
    p = tmp_path / "journal.json"; p.write_text(json.dumps(rows), encoding="utf-8")
    text = "\\n".join(prematch_singles_sections(p))
    assert "Upcoming" in text
    assert "Old Lost" not in text
    assert "Already Started" not in text


def test_background_responder_sweeps_prematch_results_without_updates(tmp_path: Path, monkeypatch):
    calls = []
    journal = tmp_path / "gool_multi_journal.json"
    monkeypatch.setattr(worker, "multi_journal_path", lambda: journal)
    monkeypatch.setattr(
        "gool_bot2.v4_prematch_settlement.reconcile_pending_prematch",
        lambda path: calls.append(("reconcile", path)) or 1,
    )
    monkeypatch.setattr(
        worker.telegram_mod,
        "_drain_prematch_result_notifications",
        lambda path: calls.append(("drain", path)) or 2,
    )
    worker._LAST_PREMATCH_RESULT_SWEEP = 0.0

    assert worker._prematch_result_sweep(force=True) == 2
    assert calls == [("reconcile", journal), ("drain", journal)]
