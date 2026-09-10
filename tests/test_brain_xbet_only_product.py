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


def test_public_menu_has_only_report_in_game_analysis(tmp_path, monkeypatch):
    monkeypatch.setattr(multi_product, "reset_public_tracking_once", lambda: None)
    monkeypatch.setattr(multi_product, "install_runtime_patches", lambda: None)
    monkeypatch.setattr(multi_product, "install_production_guard_compat", lambda: None)
    monkeypatch.setattr(multi_product, "install_brain_card_patch", lambda: None)
    monkeypatch.setattr(multi_product, "install_stale_replay_guard", lambda: None)
    monkeypatch.setattr(multi_product, "install_brain_journal_tracking", lambda: None)
    monkeypatch.setattr(multi_product, "install_production_journal_serialization", lambda: None)
    monkeypatch.setattr(multi_product, "install_result_delivery_guard", lambda: None)
    monkeypatch.setattr(multi_product, "install_journal_in_game", lambda: None)
    monkeypatch.setattr(
        multi_product,
        "repair_public_journal",
        lambda path: {"before": 0, "after": 0, "duplicates_removed": 0, "normalized": 0},
    )
    monkeypatch.setattr(multi_product, "journal_path", lambda: tmp_path / "journal.json")
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


def test_routing_only_has_brain_decision_fallback_not_journal_patches():
    source = inspect.getsource(multi_concept.routing_experts).casefold()
    assert "betdaq" not in source
    assert "install_runtime_patches" in source
    assert "install_brain_card_patch" in source
    assert "install_brain_journal_results" not in source
    assert "install_result_delivery_guard" not in source
    assert "install_journal_in_game" not in source
    assert "install_brain_in_game_patch" not in source
    assert "install_strict_in_game_live" not in source


def test_product_bootstrap_owns_single_journal_and_result_pipeline():
    source = inspect.getsource(multi_product.install_multi_product).casefold()
    assert "install_brain_journal_tracking" in source
    assert "install_brain_journal_results" not in source
    assert "install_production_journal_serialization" in source
    assert "install_result_delivery_guard" in source
    assert "install_journal_in_game" in source
    assert "repair_public_journal" in source
