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


def test_production_start_uses_expanded_multisport_keyboard(tmp_path: Path, monkeypatch):
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
    assert labels == ["📊 Отчёт", "🟢 В игре", "🎟 Ординары", "🔗 Экспрессы", "🏒 Хоккей", "🏀 Баскетбол", "📒 Хоккей", "📒 Баскет", "🔗 Хоккей экспресс", "🔗 Баскет экспресс", "🧠 Анализ", "🔎 Найти матч"]


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




def test_production_multisport_menu_buttons_are_handled(tmp_path: Path, monkeypatch):
    sent = []
    monkeypatch.setattr(
        worker,
        "_direct_send_message",
        lambda credential, chat_id, text, reply_markup=None: sent.append(text) or True,
    )
    import gool_bot2.multisport_menu as multisport_menu
    monkeypatch.setattr(multisport_menu, "sport_overview_text", lambda sport: f"SPORT:{sport}")

    journal = tmp_path / "signal_journal.json"
    a = worker._handle_direct_telegram_update(
        "test-credential", journal,
        {"update_id": 14, "message": {"chat": {"id": 123}, "text": "🏒 Хоккей"}},
    )
    b = worker._handle_direct_telegram_update(
        "test-credential", journal,
        {"update_id": 15, "message": {"chat": {"id": 123}, "text": "🏀 Баскетбол"}},
    )
    assert (a, b) == (1, 1)
    assert sent == ["SPORT:hockey", "SPORT:basketball"]

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


def test_production_prematchaudit_command_is_routed(tmp_path: Path, monkeypatch):
    sent = []
    monkeypatch.delenv("GOOL_MULTI_JOURNAL_PATH", raising=False)
    monkeypatch.setattr(
        worker,
        "_direct_send_message",
        lambda credential, chat_id, text, reply_markup=None: sent.append(text) or True,
    )
    journal = tmp_path / "signal_journal.json"
    actions = worker._handle_direct_telegram_update(
        "test-credential",
        journal,
        {"update_id": 14, "message": {"chat": {"id": 123}, "text": "/prematchaudit"}},
    )
    assert actions == 1
    assert sent
    assert "PREMATCH" in sent[0]


def test_production_livecheck_command_is_routed(tmp_path: Path, monkeypatch):
    sent = []
    monkeypatch.setattr(
        "gool_bot2.live_data_check.live_data_check_text",
        lambda: "LIVE_CHECK_OK",
    )
    monkeypatch.setattr(
        worker,
        "_direct_send_message",
        lambda credential, chat_id, text, reply_markup=None: sent.append(text) or True,
    )
    actions = worker._handle_direct_telegram_update(
        "test-credential",
        tmp_path / "signal_journal.json",
        {"update_id": 15, "message": {"chat": {"id": 123}, "text": "/livecheck"}},
    )
    assert actions == 1
    assert sent == ["LIVE_CHECK_OK"]


def test_production_prematchstatus_command_is_routed(tmp_path: Path, monkeypatch):
    sent = []
    monkeypatch.setattr(
        "gool_bot2.prematch_status.prematch_status_text",
        lambda: "PREMATCH_STATUS_OK",
    )
    monkeypatch.setattr(
        worker,
        "_direct_send_message",
        lambda credential, chat_id, text, reply_markup=None: sent.append(text) or True,
    )
    actions = worker._handle_direct_telegram_update(
        "test-credential",
        tmp_path / "signal_journal.json",
        {"update_id": 16, "message": {"chat": {"id": 123}, "text": "/prematchstatus"}},
    )
    assert actions == 1
    assert sent == ["PREMATCH_STATUS_OK"]


def test_production_valuehunter_command_is_routed(tmp_path: Path, monkeypatch):
    sent = []
    monkeypatch.delenv("GOOL_MULTI_JOURNAL_PATH", raising=False)
    monkeypatch.setattr(
        "gool_bot2.v4_value_hunter_delivery.value_hunter_report_text",
        lambda _path: "VALUE_HUNTER_OK",
    )
    monkeypatch.setattr(
        worker,
        "_direct_send_message",
        lambda credential, chat_id, text, reply_markup=None: sent.append(text) or True,
    )
    actions = worker._handle_direct_telegram_update(
        "test-credential",
        tmp_path / "signal_journal.json",
        {"update_id": 17, "message": {"chat": {"id": 123}, "text": "/valuehunter"}},
    )
    assert actions == 1
    assert sent == ["VALUE_HUNTER_OK"]



def test_production_separate_sport_journal_buttons_are_handled(tmp_path: Path, monkeypatch):
    sent = []
    monkeypatch.setattr(
        worker,
        "_direct_send_message",
        lambda credential, chat_id, text, reply_markup=None: sent.append(text) or True,
    )
    import gool_bot2.multisport_menu as multisport_menu
    monkeypatch.setattr(multisport_menu, "hockey_journal_text", lambda: "H_JOURNAL")
    monkeypatch.setattr(multisport_menu, "basketball_journal_text", lambda: "B_JOURNAL")

    journal = tmp_path / "signal_journal.json"
    a = worker._handle_direct_telegram_update(
        "test-credential", journal,
        {"update_id": 18, "message": {"chat": {"id": 123}, "text": "📒 Хоккей"}},
    )
    b = worker._handle_direct_telegram_update(
        "test-credential", journal,
        {"update_id": 19, "message": {"chat": {"id": 123}, "text": "📒 Баскет"}},
    )
    assert (a, b) == (1, 1)
    assert sent == ["H_JOURNAL", "B_JOURNAL"]



def test_production_sport_parlay_buttons_are_handled(tmp_path: Path, monkeypatch):
    sent = []
    monkeypatch.setattr(
        worker,
        "_direct_send_message",
        lambda credential, chat_id, text, reply_markup=None: sent.append(text) or True,
    )
    import gool_bot2.multisport_menu as multisport_menu
    monkeypatch.setattr(multisport_menu, "sport_parlay_text", lambda sport: f"PARLAY:{sport}")
    journal = tmp_path / "signal_journal.json"
    a = worker._handle_direct_telegram_update(
        "test-credential", journal,
        {"update_id": 30, "message": {"chat": {"id": 123}, "text": "🔗 Хоккей экспресс"}},
    )
    b = worker._handle_direct_telegram_update(
        "test-credential", journal,
        {"update_id": 31, "message": {"chat": {"id": 123}, "text": "🔗 Баскет экспресс"}},
    )
    assert (a, b) == (1, 1)
    assert sent == ["PARLAY:hockey", "PARLAY:basketball"]
