from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from gool_bot2 import money_menu


def _event(name: str, volume: float, *, hours: int, direction: float = 0.0, live: bool = False):
    now = datetime.now(timezone.utc)
    start = now.replace(hour=12, minute=0, second=0, microsecond=0) + timedelta(hours=hours)
    home, away = name.split(" — ", 1)
    level = "SUPPORT" if direction > 0 else "OPPOSITION" if direction < 0 else "NEUTRAL"
    return {
        "event_id": name,
        "home": home,
        "away": away,
        "start": start.isoformat(),
        "in_running": live,
        "volume": volume,
        "match_odds": {
            "volume": volume * 0.7,
            "runners": [
                {"name": home, "volume": volume * 0.40},
                {"name": "Draw", "volume": volume * 0.15},
                {"name": away, "volume": volume * 0.10},
            ],
        },
        "totals": {
            "FT:2.5": {
                "period": "FT",
                "line": 2.5,
                "volume": volume * 0.3,
                "flow": {
                    "level": level,
                    "direction_pp": direction,
                    "activity_volume": 125.0 if direction else 0.0,
                    "orderbook_confirmed": bool(direction),
                },
            }
        },
    }


def test_money_board_top5_today_and_direction(monkeypatch):
    monkeypatch.setenv("REPORT_TIMEZONE", "UTC")
    now = datetime.now(timezone.utc)
    events = [
        _event("A — B", 1000, hours=0, direction=2.0),
        _event("C — D", 9000, hours=0, direction=-2.5, live=True),
        _event("E — F", 8000, hours=0),
        _event("G — H", 7000, hours=0),
        _event("I — J", 6000, hours=0),
        _event("K — L", 5000, hours=0),
    ]
    tomorrow = _event("X — Y", 999999, hours=24)
    events.append(tomorrow)
    monkeypatch.setattr(
        money_menu,
        "load_matchbook_state",
        lambda: {"captured_at": now.isoformat(), "events": events},
    )

    text = money_menu.money_text()

    assert "C — D" in text
    assert "E — F" in text
    assert "G — H" in text
    assert "I — J" in text
    assert "K — L" in text
    assert "A — B" not in text
    assert "X — Y" not in text
    assert "ТМ 2.5" in text
    assert "больше всего проторговано" in text
    assert "🔴 LIVE" in text


def test_positive_flow_is_over(monkeypatch):
    monkeypatch.setenv("REPORT_TIMEZONE", "UTC")
    now = datetime.now(timezone.utc)
    monkeypatch.setattr(
        money_menu,
        "load_matchbook_state",
        lambda: {
            "captured_at": now.isoformat(),
            "events": [_event("Home — Away", 12000, hours=0, direction=3.2)],
        },
    )
    text = money_menu.money_text()
    assert "ТБ 2.5" in text
    assert "стакан подтверждает" in text


def test_neutral_volume_is_not_called_direction(monkeypatch):
    monkeypatch.setenv("REPORT_TIMEZONE", "UTC")
    now = datetime.now(timezone.utc)
    monkeypatch.setattr(
        money_menu,
        "load_matchbook_state",
        lambda: {
            "captured_at": now.isoformat(),
            "events": [_event("Home — Away", 12000, hours=0, direction=0.0)],
        },
    )
    text = money_menu.money_text()
    assert "Поток по тоталам: пока не подтверждён" in text
    assert "1X2: больше всего проторговано" in text


def test_unavailable_state_shows_real_matchbook_error(monkeypatch):
    now = datetime.now(timezone.utc)
    monkeypatch.setattr(
        money_menu,
        "load_matchbook_state",
        lambda: {
            "captured_at": now.isoformat(),
            "available": False,
            "authenticated": False,
            "error": "matchbook_auth_required status=403",
            "events": [],
        },
    )
    text = money_menu.money_text()
    assert "Matchbook API сейчас недоступен" in text
    assert "matchbook_auth_required status=403" in text
    assert "Это не означает, что матчей нет" in text


def test_live_event_is_not_hidden_by_stale_start_date(monkeypatch):
    monkeypatch.setenv("REPORT_TIMEZONE", "UTC")
    now = datetime.now(timezone.utc)
    event = _event("Live Home — Live Away", 15000, hours=-24, live=True)
    monkeypatch.setattr(
        money_menu,
        "load_matchbook_state",
        lambda: {"captured_at": now.isoformat(), "events": [event]},
    )
    text = money_menu.money_text()
    assert "Live Home — Live Away" in text
    assert "🔴 LIVE" in text


def test_existing_events_with_no_today_match_show_dates(monkeypatch):
    monkeypatch.setenv("REPORT_TIMEZONE", "UTC")
    now = datetime.now(timezone.utc)
    tomorrow = _event("Tomorrow — Match", 10000, hours=24)
    monkeypatch.setattr(
        money_menu,
        "load_matchbook_state",
        lambda: {"captured_at": now.isoformat(), "events": [tomorrow]},
    )
    text = money_menu.money_text()
    assert "В Matchbook state есть" in text
    assert "подходящих нет" in text
    assert tomorrow["start"][:10] in text


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
