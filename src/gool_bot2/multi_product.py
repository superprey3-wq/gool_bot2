from __future__ import annotations

import sys
from pathlib import Path

from . import telegram
from .brain_card_restore import install_brain_card_patch
from .brain_journal_tracking import install_brain_journal_tracking
from .brain_primary_mode import install_runtime_patches
from .journal_in_game import install_journal_in_game
from .journal_transaction_guard import install_journal_transaction_guard
from .multi_analysis_view import analysis_text as _analysis_text
from .multi_menu import journal_path, reconcile_pending, report_text
from .multi_result_reconcile import reconcile_finalized_first_half
from .multi_telegram import is_multi_telegram_active
from .production_integrity import audit_production_bindings, repair_public_journal
from .public_epoch_reset import reset_public_tracking_once
from .result_delivery_guard import install_result_delivery_guard
from .stale_replay_guard import install_stale_replay_guard


_CLEAN_MENU_KEYBOARD = {
    "keyboard": [[{"text": "📊 Отчёт"}, {"text": "🟢 В игре"}], [{"text": "🧠 Анализ"}]],
    "resize_keyboard": True,
    "is_persistent": True,
}


def _report_text_clean(*args, **kwargs) -> str:
    """Public journal contains only the ordinary Brain and 1xBet STEAM lanes."""
    path = journal_path()
    reconcile_finalized_first_half(path)
    return report_text(*args, **kwargs)


def _analysis_text_safe(*args, **kwargs) -> str:
    """Keep diagnostic comparison signs from being parsed as Telegram HTML tags."""
    text = _analysis_text(*args, **kwargs)
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
    """Install the one public production pipeline: GOOL Brain + 1xBet STEAM.

    Wiring is deliberately completed here, once, before the Telegram responder
    thread starts. No match-routing function is allowed to install/stack journal
    or result monkeypatches later.
    """
    reset_public_tracking_once()

    # Deterministic startup order. The former production bug had TWO Brain
    # journal systems installed at different times; both could settle and send
    # the same signal independently. Journal transactions are serialized before
    # Brain-primary captures any runtime function references.
    install_journal_transaction_guard()
    install_runtime_patches()
    install_brain_card_patch()
    install_stale_replay_guard()
    install_brain_journal_tracking()
    repair_public_journal(journal_path())
    install_result_delivery_guard()
    _disable_exchange_money_runtime()

    # Re-assert the exact three-button public menu. No money/exchange button is
    # appended anywhere in the active product.
    telegram.MENU_KEYBOARD = dict(_CLEAN_MENU_KEYBOARD)
    telegram.report_text = _report_text_clean
    telegram.analysis_text = _analysis_text_safe

    def _reconcile(_: Path) -> int:
        path = journal_path()
        corrected = reconcile_finalized_first_half(path)
        return corrected + reconcile_pending()

    telegram._force_reconcile_pending = _reconcile
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
            "🟢 В игре — отправленные, ещё не рассчитанные сигналы\n"
            "🧠 Анализ — текущий разбор матчей"
        )

    # Must be LAST among menu assignments: it intentionally ignores the legacy
    # SIGNAL_JOURNAL path passed by the old Telegram responder and reads the
    # canonical GOOL Multi journal instead.
    install_journal_in_game()

    # Fail closed if a future import/patch order breaks critical production
    # bindings. Running no bot is safer than sending the same result five times.
    audit_production_bindings()
    print(
        "GOOL_PRODUCT installed pipeline=unified_v1 systems=GOOL_BRAIN+1XBET_STEAM "
        "journal=single result_sender=single in_game=journal tx=on",
        flush=True,
    )
