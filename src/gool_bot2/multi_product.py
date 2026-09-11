from __future__ import annotations

import sys
from pathlib import Path

from . import telegram
from .analysis_pipeline_guard import install_analysis_pipeline_guard, pipeline_diagnostic_text
from .brain_card_restore import install_brain_card_patch
from .brain_journal_tracking import install_brain_journal_tracking
from .brain_primary_mode import install_runtime_patches
from .journal_in_game import install_journal_in_game
from .journal_report import production_report_text
from .multi_analysis_view import analysis_text as _analysis_text
from .multi_menu import journal_path, reconcile_pending
from .multi_result_reconcile import reconcile_finalized_first_half
from .multi_telegram import is_multi_telegram_active
from .orphan_pending_reconcile import reconcile_orphaned_pending
from .production_guard_compat import install_production_guard_compat
from .production_journal_repair import repair_public_journal
from .production_journal_serialization import install_production_journal_serialization
from .public_epoch_reset import reset_public_tracking_once
from .result_delivery_guard import install_result_delivery_guard
from .runtime_hardening import install_runtime_hardening
from .stale_replay_guard import install_stale_replay_guard


_CLEAN_MENU_KEYBOARD = {
    "keyboard": [[{"text": "📊 Отчёт"}, {"text": "🟢 В игре"}], [{"text": "🧠 Анализ"}]],
    "resize_keyboard": True,
    "is_persistent": True,
}


def _analysis_text_safe(*args, **kwargs) -> str:
    """Render analysis and expose a real pipeline fault instead of fake zero LIVE."""
    text = _analysis_text(*args, **kwargs)
    empty = "Сейчас нет свежих матчей для анализа" in text or "нет свежих оценок" in text
    if empty:
        diagnostic = pipeline_diagnostic_text()
        if diagnostic:
            text = diagnostic
    return text.replace("PRICE<", "PRICE&lt;").replace("RATING<", "RATING&lt;")


def _disable_exchange_money_runtime() -> None:
    """Hard-disable legacy exchange FLOW emitters inside the production worker."""
    modules = [
        sys.modules.get("gool_bot2.storage_market_signal_worker_var"),
        sys.modules.get("__main__"),
    ]
    for module in modules:
        if module is None:
            continue
        spec = getattr(module, "__spec__", None)
        spec_name = str(getattr(spec, "name", "") or "")
        module_name = str(getattr(module, "__name__", "") or "")
        if module_name == "__main__" and spec_name not in {"", "gool_bot2.storage_market_signal_worker_var"}:
            continue
        if hasattr(module, "_maybe_emit_money_flow"):
            setattr(module, "_maybe_emit_money_flow", lambda _record: None)
        if hasattr(module, "daily_report_due_date"):
            setattr(module, "daily_report_due_date", lambda *_args, **_kwargs: None)


def install_multi_product() -> None:
    """Install the complete two-system product once, in deterministic order.

    Production has one ordinary GOOL Brain journal pipeline and one autonomous
    1xBet STEAM pipeline. The previous lazy installation mixed two separate Brain
    journal bridges and several result senders; this startup sequence makes the
    journal, settlement, result delivery and menu ownership explicit before the
    Telegram responder thread starts.
    """
    reset_public_tracking_once()

    # Decision/card routing first. The compatibility wrapper preserves the
    # original LIVE-only helper for non-RouterDecision diagnostic calls.
    install_runtime_patches()
    install_production_guard_compat()
    install_brain_card_patch()
    install_stale_replay_guard()
    install_brain_journal_tracking()

    # From here on every journal read-modify-write transaction is serialized
    # across the LIVE worker and Telegram responder. Then repair old dual-pipeline
    # rows before any public menu/result sender reads them.
    install_production_journal_serialization()
    repair = repair_public_journal(journal_path())
    install_result_delivery_guard()
    _disable_exchange_money_runtime()

    # runtime_hardening owns the production _process implementation. Install it
    # before the health wrapper so no later monkeypatch can silently remove the
    # watchdog/heartbeat layer. storage_market_signal_worker_var calls the same
    # installer again after this function; it is idempotent.
    install_runtime_hardening()
    install_analysis_pipeline_guard()

    telegram.MENU_KEYBOARD = dict(_CLEAN_MENU_KEYBOARD)
    telegram.report_text = production_report_text
    telegram.analysis_text = _analysis_text_safe

    def _reconcile(_: Path) -> int:
        path = journal_path()
        corrected = reconcile_finalized_first_half(path)
        regular = reconcile_pending()
        orphaned = reconcile_orphaned_pending(path)
        return int(corrected or 0) + int(regular or 0) + int(orphaned or 0)

    # Every menu command performs settlement exactly once before rendering.
    # Both Report and In Game are read-only views after this point.
    telegram._force_reconcile_pending = _reconcile
    install_journal_in_game()

    if is_multi_telegram_active():
        telegram.START_TEXT = (
            "🟢 <b>GOOL работает</b>\n\n"
            "Активные системы:\n"
            "🧠 GOOL Brain — обычные LIVE-сигналы по футболу\n"
            "🔥 1xBet STEAM — отдельные сигналы прогруза\n\n"
            "📊 Отчёт — журнал Brain + STEAM\n"
            "🟢 В игре — отправленные, ещё не рассчитанные сигналы\n"
            "🧠 Анализ — текущий разбор матчей"
        )
    else:
        telegram.START_TEXT = (
            "🟢 <b>GOOL работает в shadow</b>\n\n"
            "Активные системы: GOOL Brain + 1xBet STEAM.\n\n"
            "📊 Отчёт — журнал\n"
            "🟢 В игре — активные сигналы\n"
            "🧠 Анализ — текущий разбор матчей"
        )

    print(
        "GOOL_PRODUCT_PIPELINE installed version=2026-09-11-analysis-watchdog "
        f"journal_before={repair['before']} journal_after={repair['after']} "
        f"duplicates_removed={repair['duplicates_removed']}",
        flush=True,
    )
