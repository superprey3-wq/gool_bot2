from __future__ import annotations

import inspect

import monkey_start

from gool_bot2 import multi_concept, multi_product, telegram


def test_production_supervisor_starts_no_exchange_workers(monkeypatch):
    monkeypatch.setenv("LIVE_INTERVAL_SECONDS", "60")
    monkeypatch.setenv("XBET_MARKET_INTERVAL_SECONDS", "15")

    commands = monkey_start._production_commands(False)

    assert set(commands) == {"live", "xbet", "worker"}
    joined = " ".join(" ".join(command) for command in commands.values()).lower()
    assert "matchbook" not in joined
    assert "betdaq" not in joined
    assert "sxbet" not in joined


def test_browser_is_support_process_not_signal_system():
    commands = monkey_start._production_commands(True)
    assert set(commands) == {"live", "xbet", "worker", "browser"}


def test_public_menu_has_only_report_in_game_analysis(monkeypatch):
    monkeypatch.setattr(multi_product, "reset_public_tracking_once", lambda: None)
    monkeypatch.setattr(multi_product, "is_multi_telegram_active", lambda: True)

    multi_product.install_multi_product()

    texts = [
        button["text"]
        for row in telegram.MENU_KEYBOARD["keyboard"]
        for button in row
    ]
    assert texts == ["📊 Отчёт", "🟢 В игре", "🧠 Анализ"]
    assert all("деньги" not in text.casefold() for text in texts)
    assert "BETDAQ" not in telegram.START_TEXT
    assert "MONEY FLOW" not in telegram.START_TEXT
    assert "1xBet STEAM" in telegram.START_TEXT
    assert "GOOL Brain" in telegram.START_TEXT


def test_active_routing_does_not_install_betdaq_menu():
    source = inspect.getsource(multi_concept.routing_experts).casefold()
    assert "betdaq" not in source
    assert "install_brain_card_patch" in source
    assert "install_brain_in_game_patch" in source
