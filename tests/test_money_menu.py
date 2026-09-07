from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from gool_bot2 import money_menu
from gool_bot2.betfair_public_board import attach_price_flow, parse_betfair_html


def _runner(label: str, back: float, lay: float):
    return {
        "label": label,
        "best_back": {"odd": back, "odds": back, "available_gbp": 100.0},
        "best_lay": {"odd": lay, "odds": lay, "available_gbp": 100.0},
    }


def _event(
    name: str,
    matched: float,
    *,
    start: datetime | None = None,
    live: bool = False,
    flow=None,
):
    dt = start or datetime.now(timezone.utc).replace(hour=20, minute=0, second=0, microsecond=0)
    return {
        "event_id": name,
        "name": name,
        "home": name.split(" ", 1)[0],
        "away": name.split(" ", 1)[-1],
        "start": dt.isoformat(),
        "in_running": live,
        "matched_gbp": matched,
        "runners": [_runner("П1", 2.0, 2.02), _runner("X", 3.4, 3.45), _runner("П2", 4.0, 4.1)],
        "flow": flow or {"ready": False, "level": "WARMING"},
    }


def test_parse_public_betfair_anchor_row():
    html = """
    <html><body>
      <a href="/exchange/plus/en/football/english-league/bromley-v-afc-wimbledon-betting-123">
        Today 19:00 Bromley AFC Wimbledon 0 Unmatched bets 0 Matched bets£9,039
        2.64 £166 2.66 £164 3.5 £115 3.55 £296 2.96 £40 3 £191
      </a>
    </body></html>
    """
    rows = parse_betfair_html(html)
    assert len(rows) == 1
    row = rows[0]
    assert row["name"] == "Bromley AFC Wimbledon"
    assert row["matched_gbp"] == 9039.0
    assert row["start_label"] == "Today 19:00"
    assert [x["label"] for x in row["runners"]] == ["П1", "X", "П2"]
    assert row["runners"][0]["best_back"]["odd"] == 2.64
    assert row["runners"][2]["best_lay"]["odd"] == 3.0


def test_price_shortening_plus_new_matched_creates_flow():
    old = {
        "event_key": "home-away",
        "name": "Home Away",
        "matched_gbp": 10000,
        "runners": [_runner("П1", 2.0, 2.02), _runner("X", 3.4, 3.45), _runner("П2", 4.0, 4.1)],
    }
    new = dict(old)
    new["matched_gbp"] = 10800
    new["runners"] = [_runner("П1", 1.88, 1.90), _runner("X", 3.6, 3.65), _runner("П2", 4.5, 4.6)]
    out = attach_price_flow(old, new)
    assert out["flow"]["ready"] is True
    assert out["flow"]["outcome"] == "П1"
    assert out["flow"]["level"] in {"FLOW", "STRONG_FLOW"}
    assert out["flow"]["matched_delta_gbp"] == 800.0


def test_money_board_top5_today_and_direction(monkeypatch):
    monkeypatch.setenv("REPORT_TIMEZONE", "UTC")
    now = datetime.now(timezone.utc)
    flow = {
        "ready": True,
        "level": "STRONG_FLOW",
        "outcome": "П1",
        "matched_delta_gbp": 800.0,
        "implied_delta_pp": 2.1,
        "old_odd": 2.1,
        "new_odd": 1.95,
    }
    events = [
        _event("A B", 1000, start=now),
        _event("C D", 9000, start=now, live=True, flow=flow),
        _event("E F", 8000, start=now),
        _event("G H", 7000, start=now),
        _event("I J", 6000, start=now),
        _event("K L", 5000, start=now),
        _event("Tomorrow Match", 999999, start=now + timedelta(days=1)),
    ]
    monkeypatch.setattr(
        money_menu,
        "load_betdaq_state",
        lambda: {
            "captured_at": now.isoformat(),
            "available": True,
            "events": events,
            "tracked_events": len(events),
            "tracked_markets": 21,
        },
    )

    text = money_menu.money_text()

    assert "C D" in text
    assert "E F" in text
    assert "G H" in text
    assert "I J" in text
    assert "K L" in text
    assert "A B" not in text
    assert "Tomorrow Match" not in text
    assert "Направление: <b>П1</b>" in text
    assert "🔴 LIVE" in text
    assert "BETDAQ Exchange" in text
    assert "anonymous AAPI" in text


def test_empty_betdaq_board_shows_stream_diagnostics(monkeypatch):
    now = datetime.now(timezone.utc)
    monkeypatch.setattr(
        money_menu,
        "load_betdaq_state",
        lambda: {
            "captured_at": now.isoformat(),
            "available": False,
            "error": "RuntimeError:stream_closed",
            "events": [],
        },
    )
    text = money_menu.money_text()
    assert "BETDAQ stream сейчас недоступен" in text
    assert "RuntimeError:stream_closed" in text


def test_live_event_is_shown_even_if_start_is_not_today(monkeypatch):
    now = datetime.now(timezone.utc)
    event = _event("Live Home Away", 15000, start=now - timedelta(days=1), live=True)
    monkeypatch.setattr(
        money_menu,
        "load_betdaq_state",
        lambda: {"captured_at": now.isoformat(), "available": True, "events": [event], "tracked_events": 1, "tracked_markets": 3},
    )
    text = money_menu.money_text()
    assert "Live Home Away" in text
    assert "🔴 LIVE" in text


def test_money_button_install_is_idempotent():
    fake = SimpleNamespace(
        MENU_KEYBOARD={
            "keyboard": [[{"text": "📊 Отчёт"}, {"text": "🟢 В игре"}], [{"text": "🧠 Анализ"}]],
            "resize_keyboard": True,
        }
    )
    money_menu.install_money_button(fake)
    money_menu.install_money_button(fake)
    buttons = [button["text"] for row in fake.MENU_KEYBOARD["keyboard"] for button in row]
    assert buttons.count("💰 Деньги") == 1
    assert fake.MENU_KEYBOARD["keyboard"][-1][-1]["text"] == "💰 Деньги"
