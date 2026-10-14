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


def test_prematch_notification_claim_does_not_capture_live_rows(tmp_path):
    from gool_bot2.journal import save_signal_journal, load_signal_journal
    from gool_bot2.multi_delivery import pending_result_notifications
    path = tmp_path / "journal.json"
    rows = [
        {"entry_key":"live:1","origin":"live","mode":"active","result":"won","telegram_sent":True,"result_notification_pending":True,"result_notification_created_at":"2026-09-29T12:00:00+00:00"},
        {"entry_id":"prematch:1","origin":"prematch","result":"won","telegram_sent":True,"result_notification_pending":True,"result_notification_created_at":"2026-09-29T12:00:00+00:00"},
    ]
    save_signal_journal(path, rows)
    claimed = pending_result_notifications(path, origins={"prematch","prematch_parlay","parlay"})
    assert [row.get("entry_id") for row in claimed] == ["prematch:1"]
    stored = load_signal_journal(path)
    live = next(row for row in stored if row.get("origin") == "live")
    assert not live.get("result_notification_claim_id")


def test_prematch_result_watchdog_delivers_without_menu_poll(tmp_path, monkeypatch):
    from gool_bot2.journal import save_signal_journal, load_signal_journal
    from gool_bot2 import v4_prematch_delivery as delivery
    path = tmp_path / "journal.json"
    row = {
        "entry_id":"prematch:done","origin":"prematch","card_family":"prematch_single",
        "result":"won","telegram_sent":True,"result_notification_pending":True,
        "result_notification_created_at":"2026-09-29T12:00:00+00:00",
        "home":"A","away":"B","market":"FT_OVER_2.5","selection":"over 2.5","odd":1.7,
        "settled_score":[2,1],
    }
    save_signal_journal(path, [row])
    monkeypatch.setattr("gool_bot2.v4_prematch_settlement.reconcile_pending_prematch", lambda p: 0)
    monkeypatch.setattr(delivery, "emit_prematch_result", lambda row, record=None: 1)
    result = delivery.reconcile_and_deliver_prematch_results(path)
    assert result == {"settled":0,"delivered":1}
    stored = load_signal_journal(path)[0]
    assert stored["result_notification_pending"] is False
    assert stored["result_telegram_sent"] is True


def test_same_fixture_cannot_create_second_public_prematch_single(tmp_path):
    path = tmp_path / "journal.json"
    first = append_prematch_entry(path, {
        "entry_id": "prematch:e2:FT_OVER_2.5",
        "event_id": "e2", "match_id": "e2",
        "home": "A", "away": "B",
        "market": "FT_OVER_2.5", "selection": "FT_OVER_2.5",
        "odd": 1.70,
    })
    second = append_prematch_entry(path, {
        "entry_id": "prematch:e2:match_total",
        "event_id": "e2", "match_id": "e2",
        "home": "A", "away": "B",
        "market": "match_total", "selection": "over 2.5",
        "odd": 1.68,
    })
    rows = load_signal_journal(path)
    assert len(rows) == 1
    assert second["entry_id"] == first["entry_id"]
    assert rows[0]["market"] == "FT_OVER_2.5"


def test_only_one_super_is_publicly_sent_per_moscow_day(tmp_path, monkeypatch):
    from gool_bot2 import v4_prematch_delivery as delivery
    from gool_bot2.v4_prematch_engine import PrematchPick

    journal = tmp_path / "journal.json"
    sent = []
    monkeypatch.setattr(delivery, "render_v4_parlay_card", lambda *args, **kwargs: b"png")
    monkeypatch.setattr(
        delivery.telegram,
        "broadcast_photo",
        lambda png, caption="", reply_markup=None: sent.append(caption) or 1,
    )

    def ticket(prefix):
        legs = [
            PrematchPick(
                f"{prefix}{i}", f"H{i}", f"A{i}", "match_total", "over 1.5",
                1.30, .85, .75, .90, "L", 1790950000.0 + i,
            )
            for i in range(10)
        ]
        return {
            "mode": "SUPER",
            "super": {
                "kind": "SUPER",
                "legs": legs,
                "combined_probability": .20,
                "combined_odds": 13.79,
            },
            "doubles": [],
            "singles": [],
        }, {p.event_id: {"bookmaker": "Test"} for p in legs}

    first, meta1 = ticket("a")
    second, meta2 = ticket("b")

    out1 = delivery.emit_delivery_selection(first, meta1, journal)
    out2 = delivery.emit_delivery_selection(second, meta2, journal)

    assert out1["parlays"] == 1
    assert out2["parlays"] == 0
    assert len(sent) == 1
    rows = load_signal_journal(journal)
    supers = [r for r in rows if str(r.get("kind") or "").upper() == "SUPER"]
    assert len(supers) == 1
    assert supers[0]["telegram_sent"] is True



def test_fixture_is_not_reused_across_public_doubles_same_day(tmp_path, monkeypatch):
    from gool_bot2 import v4_prematch_delivery as delivery
    from gool_bot2.v4_prematch_engine import PrematchPick

    journal = tmp_path / "journal.json"
    sent = []
    monkeypatch.setattr(delivery, "render_v4_parlay_card", lambda *args, **kwargs: b"png")
    monkeypatch.setattr(
        delivery.telegram,
        "broadcast_photo",
        lambda png, caption="", reply_markup=None: sent.append(caption) or 1,
    )

    def pick(event_id, home):
        return PrematchPick(
            event_id, home, "Away", "match_total", "over 1.5",
            1.45, .82, .68, .90, "L", 1790950000.0,
        )

    a, b, c = pick("a", "A"), pick("b", "B"), pick("c", "C")
    first = {
        "mode": "DOUBLES", "super": None, "singles": [],
        "doubles": [{"legs": [a, b], "combined_probability": .60, "combined_odds": 2.10}],
    }
    second = {
        "mode": "DOUBLES", "super": None, "singles": [],
        "doubles": [{"legs": [a, c], "combined_probability": .59, "combined_odds": 2.12}],
    }
    meta = {p.event_id: {"bookmaker": "Test"} for p in (a, b, c)}

    out1 = delivery.emit_delivery_selection(first, meta, journal)
    out2 = delivery.emit_delivery_selection(second, meta, journal)

    assert out1["parlays"] == 1
    assert out2["parlays"] == 0
    assert len(sent) == 1


def test_elite_safe_tournament_resends_new_revision_once_without_erasing_old(tmp_path, monkeypatch):
    from gool_bot2 import v4_prematch_delivery as delivery
    from gool_bot2.journal import load_signal_journal, save_signal_journal
    from gool_bot2.v4_prematch_engine import PrematchPick
    from gool_bot2.tournament_express import league_key

    path = tmp_path / "journal.json"
    day = "2026-10-14"
    league = "EUROPE: Champions League - League phase"
    legacy_id = f"parlay:tournament:{day}:{league_key(league)}"
    original = {
        "entry_id": legacy_id, "origin": "prematch_parlay",
        "kind": "TOURNAMENT", "tournament": league, "telegram_sent": True,
        "result": "pending", "odd": 168.24,
        "legs": [{"event_id": "a", "selection": "under 1.5", "odd": 2.33}],
    }
    save_signal_journal(path, [original])
    cards = []
    monkeypatch.setattr(delivery, "render_v4_parlay_card", lambda parent, **kw: b"new-png")
    monkeypatch.setattr(
        delivery.telegram, "broadcast_photo",
        lambda png, caption="", **kw: cards.append((png, caption)) or 1,
    )
    picks = [
        PrematchPick(f"id{i}", f"Home{i}", f"Away{i}", "match_total",
                    "over 1.5", 1.30, .73, .73, .60, league, 1792000000.0)
        for i in range(3)
    ]
    product = {"mode": "SINGLES", "singles": [], "doubles": [],
               "tournaments": [{
                   "kind": "TOURNAMENT", "day": day,
                   "tournament": league, "legs": picks,
                   "combined_odds": round(1.30**3, 3),
                   "combined_probability": round(.73**3, 6),
                   "coverage_only_count": 3, "verified_value_count": 0,
                   "coverage_only_legs": [(p.event_id, p.market, p.selection) for p in picks],
               }]}
    meta = {p.event_id: {"bookmaker": "1xBet"} for p in picks}

    first = delivery.emit_delivery_selection(product, meta, path)
    second = delivery.emit_delivery_selection(product, meta, path)
    assert first["parlays"] == 1
    assert second["parlays"] == 0
    assert len(cards) == 1
    assert "ОБНОВЛЁННЫЙ КУПОН" in cards[0][1]
    rows = load_signal_journal(path)
    assert len(rows) == 2
    old = next(x for x in rows if x["entry_id"] == legacy_id)
    updated = next(x for x in rows if x["entry_id"] != legacy_id)
    assert old["odd"] == 168.24 and old["telegram_sent"] is True
    assert updated["replaces_entry_id"] == legacy_id
    assert updated["ticket_revision"] == "elite_safe_v1"
    assert updated["telegram_sent"] is True
    assert updated["odd"] < 3.0
    assert all(l["leg_qualification"] == "COVERAGE_ONLY" for l in updated["legs"])


def test_stale_nonelite_tournament_delivery_is_blocked_before_telegram(tmp_path, monkeypatch):
    from gool_bot2 import v4_prematch_delivery as delivery
    from gool_bot2.v4_prematch_engine import PrematchPick

    cards = []
    monkeypatch.setattr(delivery, "render_v4_parlay_card", lambda *a, **kw: b"png")
    monkeypatch.setattr(delivery.telegram, "broadcast_photo",
                        lambda *a, **kw: cards.append(a) or 1)
    pick = PrematchPick("e", "A", "B", "match_total", "over 1.5",
                       1.35, .75, .70, .70, "BRAZIL: Serie A")
    delivery_payload = {"singles": [], "doubles": [], "tournaments": [{
        "tournament": "BRAZIL: Serie A", "day": "2026-10-14",
        "legs": [pick], "combined_odds": 1.35, "combined_probability": .75,
    }]}
    result = delivery.emit_delivery_selection(delivery_payload, {}, tmp_path/"journal.json")
    assert result["parlays"] == 0
    assert cards == []



def test_safe_release_resends_one_revised_tournament_and_keeps_old_wager(tmp_path, monkeypatch):
    from gool_bot2 import v4_prematch_delivery as delivery
    from gool_bot2.journal import load_signal_journal, save_signal_journal
    from gool_bot2.tournament_express import league_key
    from gool_bot2.v4_prematch_engine import PrematchPick

    journal = tmp_path / "journal.json"
    league = "EUROPE: Champions League - League phase"
    old_id = f"parlay:tournament:2026-10-14:{league_key(league)}"
    old_legs = [
        delivery.prematch_row_from_pick(PrematchPick(
            str(i), f"Home {i}", f"Away {i}", "match_total", "under 1.5",
            2.33, .7, .4, .85, league, 1800000000.0
        ))
        for i in range(2)
    ]
    save_signal_journal(journal, [{
        "entry_id": old_id, "origin": "prematch_parlay",
        "kind": "TOURNAMENT", "result": "pending", "telegram_sent": True,
        "legs": old_legs, "odd": 5.43,
    }])
    new_picks = [
        PrematchPick(str(i), f"Home {i}", f"Away {i}", "match_total",
                     "over 1.5", 1.35, .76, .72, .85, league, 1800000000.0)
        for i in range(2)
    ]
    ticket = {
        "mode": "NO_BET", "singles": [], "doubles": [],
        "tournaments": [{
            "tournament": league, "day": "2026-10-14", "legs": new_picks,
            "coverage_only_count": 2,
            "coverage_only_legs": [(p.event_id, p.market, p.selection) for p in new_picks],
            "verified_value_count": 0,
            "combined_odds": 1.8225, "combined_probability": .5,
        }],
    }
    messages = []
    monkeypatch.setattr(delivery, "render_v4_parlay_card", lambda *_a, **_kw: b"png")
    monkeypatch.setattr(delivery.telegram, "broadcast_photo",
                        lambda _png, caption="", **_kw: messages.append(caption) or 1)

    first = delivery.emit_delivery_selection(ticket, {}, journal)
    second = delivery.emit_delivery_selection(ticket, {}, journal)
    assert first["parlays"] == 1
    assert second["parlays"] == 0
    assert len(messages) == 1
    assert "ОБНОВЛЁННЫЙ" in messages[0]
    assert "уже сделанные ставки не отменяются" in messages[0]
    rows = load_signal_journal(journal)
    assert len(rows) == 2
    old = next(row for row in rows if row["entry_id"] == old_id)
    new = next(row for row in rows if row["entry_id"] != old_id)
    assert new["telegram_sent"] is True
    assert new["replaces_entry_id"] == old_id
    assert old["suggestion_status"] == "previous_version"
    assert old["superseded_by"] == new["entry_id"]
    assert old["odd"] == 5.43 and old["result"] == "pending"
    assert [x["selection"] for x in old["legs"]] == ["under 1.5", "under 1.5"]
    assert [x["selection"] for x in new["legs"]] == ["over 1.5", "over 1.5"]


def test_safe_release_does_not_resend_identical_existing_tournament(tmp_path, monkeypatch):
    from gool_bot2 import v4_prematch_delivery as delivery
    from gool_bot2.journal import load_signal_journal, save_signal_journal
    from gool_bot2.tournament_express import league_key
    from gool_bot2.v4_prematch_engine import PrematchPick

    path = tmp_path / "journal.json"
    league = "ENGLAND: Premier League"
    old_id = f"parlay:tournament:2026-10-14:{league_key(league)}"
    picks = [
        PrematchPick(str(i), f"H{i}", f"A{i}", "match_total", "over 1.5",
                     1.40, .75, .68, .85, league, 1800000000.0)
        for i in range(2)
    ]
    save_signal_journal(path, [{
        "entry_id": old_id, "origin": "prematch_parlay",
        "kind": "TOURNAMENT", "result": "pending", "telegram_sent": True,
        "legs": [delivery.prematch_row_from_pick(p) for p in picks], "odd": 1.96,
    }])
    calls = []
    monkeypatch.setattr(delivery, "render_v4_parlay_card", lambda *_a, **_kw: b"png")
    monkeypatch.setattr(delivery.telegram, "broadcast_photo",
                        lambda *args, **kw: calls.append(args) or 1)
    input_ = {"tournaments": [{
        "tournament": league, "day": "2026-10-14", "legs": picks,
        "combined_odds": 1.96, "combined_probability": .5,
    }]}
    result = delivery.emit_delivery_selection(input_, {}, path)
    assert result["parlays"] == 0
    assert calls == []
    assert len(load_signal_journal(path)) == 1
