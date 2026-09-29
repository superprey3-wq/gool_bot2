from pathlib import Path

from gool_bot2.journal import load_signal_journal
from gool_bot2.v4_prematch_delivery import (
    append_prematch_entry,
    mark_prematch_in_game,
    parlay_leg_states,
    prematch_keyboard,
)


def test_prematch_entry_is_same_journal_row_when_entered(tmp_path):
    path = tmp_path / "journal.json"
    entry = append_prematch_entry(path, {
        "event_id": "e1", "home": "A", "away": "B", "market": "ТБ 2.5", "odd": 1.8,
    })
    assert mark_prematch_in_game(path, entry["entry_id"], chat_id=123)
    rows = load_signal_journal(path)
    assert len(rows) == 1
    assert rows[0]["origin"] == "prematch"
    assert rows[0]["in_game"] is True
    assert rows[0]["entered_by_chat_id"] == "123"


def test_prematch_keyboard_uses_v4_callback():
    data = prematch_keyboard("abc")["inline_keyboard"][0][0]
    assert data["callback_data"] == "v4ig:abc"
    assert "В игре" in data["text"]


def test_parlay_legs_keep_individual_live_states():
    parlay = {"legs": [{"event_id": "a"}, {"event_id": "b"}]}
    live = [{"event_id": "a", "lifecycle": "in_game", "current_minute": 22, "current_score": [1, 0]}]
    states = parlay_leg_states(parlay, live)
    assert states[0]["lifecycle"] == "in_game"
    assert states[0]["current_minute"] == 22
    assert states[1].get("lifecycle") is None


def test_scheduled_prematch_is_not_in_game(tmp_path):
    from gool_bot2.journal import save_signal_journal
    from gool_bot2.journal_in_game import journal_in_game_sections
    path=tmp_path/"journal.json"
    save_signal_journal(path,[{"mode":"active","origin":"prematch","result":"pending","telegram_sent":True,"lifecycle":"scheduled","home":"A","away":"B","market":"ТМ 2.5","odd":1.5}])
    assert "Открытых сигналов сейчас нет" in journal_in_game_sections(path)[0]


def test_parlay_parent_is_never_rendered_as_match_in_game(tmp_path):
    from gool_bot2.journal import save_signal_journal
    from gool_bot2.journal_in_game import journal_in_game_sections
    path=tmp_path/"journal.json"
    save_signal_journal(path,[{"mode":"active","origin":"prematch_parlay","result":"pending","telegram_sent":True,"home":"","away":"","odd":2.2}])
    assert "Открытых сигналов сейчас нет" in journal_in_game_sections(path)[0]


def test_prematch_result_delivery_finalizes_by_entry_id(tmp_path):
    from gool_bot2.journal import save_signal_journal, load_signal_journal
    from gool_bot2.multi_delivery import pending_result_notifications, finalize_result_delivery
    path=tmp_path/"journal.json"
    row={"entry_id":"prematch:abc:FT_UNDER_2.5","origin":"prematch","mode":"active",
         "result":"won","telegram_sent":True,"result_notification_pending":True,
         "result_notification_created_at":"2026-09-27T20:00:00+00:00"}
    save_signal_journal(path,[row])
    claimed=pending_result_notifications(path)
    assert len(claimed)==1
    assert finalize_result_delivery(path,claimed[0],1) is True
    stored=load_signal_journal(path)[0]
    assert stored["result_notification_pending"] is False
    assert stored["result_telegram_sent"] is True
    assert pending_result_notifications(path)==[]


def test_prematch_row_keeps_flashscore_team_metadata():
    from gool_bot2.v4_prematch_delivery import prematch_row_from_pick
    from gool_bot2.v4_prematch_engine import PrematchPick
    pick = PrematchPick("e-logo", "Home", "Away", "FT_OVER_2.5", "FT_OVER_2.5", 1.7, .72, .60, .9)
    meta = {"home_team_id":"h1","away_team_id":"a1","home_team_slug":"home","away_team_slug":"away"}
    row = prematch_row_from_pick(pick, flashscore_meta=meta)
    assert row["flashscore_meta"] == meta


def test_result_claims_are_scoped_by_origin(tmp_path):
    from gool_bot2.journal import save_signal_journal, load_signal_journal
    from gool_bot2.multi_delivery import pending_result_notifications
    path = tmp_path / "journal.json"
    rows = [
        {"entry_id":"p1","origin":"prematch","result":"won","telegram_sent":True,"result_notification_pending":True,"result_notification_created_at":"2026-09-29T10:00:00+00:00"},
        {"entry_key":"l1","origin":"live","mode":"active","result":"won","telegram_sent":True,"result_notification_pending":True,"result_notification_created_at":"2026-09-29T10:00:00+00:00"},
    ]
    save_signal_journal(path, rows)
    claimed = pending_result_notifications(path, origins={"prematch","prematch_parlay","parlay"})
    assert [row["entry_id"] for row in claimed] == ["p1"]
    stored = load_signal_journal(path)
    assert stored[0].get("result_notification_claim_id")
    assert not stored[1].get("result_notification_claim_id")


def test_prematch_result_drain_sends_and_finalizes(tmp_path, monkeypatch):
    from gool_bot2.journal import save_signal_journal, load_signal_journal
    import gool_bot2.v4_prematch_delivery as delivery
    path = tmp_path / "journal.json"
    row = {"entry_id":"p2","origin":"prematch","result":"won","telegram_sent":True,
           "result_notification_pending":True,"result_notification_created_at":"2026-09-29T10:00:00+00:00"}
    save_signal_journal(path, [row])
    monkeypatch.setattr(delivery, "emit_prematch_result", lambda row, record=None: 1)
    assert delivery.drain_prematch_result_notifications(path) == 1
    stored = load_signal_journal(path)[0]
    assert stored["result_notification_pending"] is False
    assert stored["result_telegram_sent"] is True


def test_prematch_monitor_settles_before_delivery(tmp_path, monkeypatch):
    import gool_bot2.v4_prematch_daemon as daemon
    calls = []
    monkeypatch.setattr(daemon, "reconcile_pending_prematch", lambda path: calls.append("settle") or 1)
    monkeypatch.setattr(daemon, "drain_prematch_result_notifications", lambda path: calls.append("send") or 1)
    daemon._monitor_results(tmp_path / "journal.json")
    assert calls == ["settle", "send"]
