from __future__ import annotations

import importlib.util
import inspect
from pathlib import Path

from gool_bot2 import multi_concept, multi_product, telegram


def _monkey_start_module():
    path = Path(__file__).resolve().parents[1] / "monkey_start.py"
    spec = importlib.util.spec_from_file_location("gool_test_monkey_start", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_production_supervisor_starts_no_exchange_workers(monkeypatch):
    monkeypatch.setenv("LIVE_INTERVAL_SECONDS", "60")
    monkeypatch.setenv("XBET_MARKET_INTERVAL_SECONDS", "15")
    monkey_start = _monkey_start_module()

    commands = monkey_start._production_commands(False)

    assert set(commands) == {"live", "xbet", "worker"}
    joined = " ".join(" ".join(command) for command in commands.values()).lower()
    assert "matchbook" not in joined
    assert "betdaq" not in joined
    assert "sxbet" not in joined


def test_browser_is_support_process_not_signal_system():
    monkey_start = _monkey_start_module()
    commands = monkey_start._production_commands(True)
    assert set(commands) == {"live", "xbet", "worker", "browser"}


def test_public_menu_has_only_report_in_game_analysis(monkeypatch):
    monkeypatch.setattr(multi_product, "reset_public_tracking_once", lambda: None)
    monkeypatch.setattr(multi_product, "is_multi_telegram_active", lambda: True)
    monkeypatch.setattr(multi_product, "repair_public_journal", lambda path: {})
    monkeypatch.setattr(multi_product, "audit_production_bindings", lambda: {})

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


def test_routing_is_side_effect_free_and_product_owns_wiring():
    routing = inspect.getsource(multi_concept.routing_experts).casefold()
    product = inspect.getsource(multi_product.install_multi_product).casefold()

    assert "betdaq" not in routing
    assert "install_" not in routing
    assert "install_runtime_patches" in product
    assert "install_brain_card_patch" in product
    assert "install_brain_journal_tracking" in product
    assert "install_pending_reconcile_guard" in product
    assert "install_result_delivery_guard" in product
    assert "install_journal_in_game" in product
    assert "install_brain_journal_results" not in product
    assert "install_brain_in_game_patch" not in product
    assert "install_strict_in_game_live" not in product
