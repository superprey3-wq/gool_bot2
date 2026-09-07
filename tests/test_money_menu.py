from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace

from gool_bot2 import money_menu
from gool_bot2.betfair_public_board import attach_price_flow, parse_betfair_html


def _runner(label: str, back: float, lay: float):
    return {
        "label": label,
        "best_back": {"odd": back, "available_gbp": 100.0},
        "best_lay": {"odd": lay, "available_gbp": 100.0},
    }


def _event(name: str, matched: float, *, label: str = "Today 20:00", live: bool = False, flow=None):
    return {
        "event_key": f"https://www.betfair.com/exchange/plus/en/football/test/{name}",
        "url": f"https://www.betfair.com/exchange/plus/en/football/test/{name}",
        "name": name,
        "in_running": live,
        "start_label": "LIVE" if live else label,
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
    old = _event("Home Away", 10000)
    new = _event("Home Away", 10800)
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
        _event("A B", 1000),
        _event("C D", 9000, live=True, flow=flow),
        _event("E F", 8000),
        _event("G H", 7000),
        _event("I J", 6000),
        _event("K L", 5000),
        _event("Tomorrow Match", 999999, label="Tomorrow 20:00"),
    ]
    monkeypatch.setattr(
        money_menu,
        "load_betfair_state",
        lambda: {"captured_at": now.isoformat(), "available": True, "events": events},
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
    assert "Betfair Exchange PUBLIC" in text


def test_empty_public_board_shows_http_diagnostics(monkeypatch):
    now = datetime.now(timezone.utc)
    monkeypatch.setattr(
        money_menu,
        "load_betfair_state",
        lambda: {
            "captured_at": now.isoformat(),
            "available": False,
            "statuses": ["403:all", "403:inplay"],
            "error": "HTTPError:Forbidden",
            "events": [],
        },
    )
    text = money_menu.money_text()
    assert "Публичная Betfair-доска пока не распознана" in text
    assert "403:all" in text
    assert "HTTPError:Forbidden" in text


def test_live_event_is_shown_even_without_today_label(monkeypatch):
    now = datetime.now(timezone.utc)
    event = _event("Live Home Away", 15000, label="Sep 6 12:00", live=True)
    monkeypatch.setattr(
        money_menu,
        "load_betfair_state",
        lambda: {"captured_at": now.isoformat(), "available": True, "events": [event]},
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
