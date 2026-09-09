from __future__ import annotations

import sys
from pathlib import Path

from . import telegram
from .brain_journal_tracking import install_brain_journal_tracking
from .multi_analysis_view import analysis_text as _analysis_text
from .multi_menu import in_game_sections as _multi_in_game_sections
from .multi_menu import journal_path, reconcile_pending, report_text
from .multi_result_reconcile import reconcile_finalized_first_half
from .multi_telegram import is_multi_telegram_active
from .public_epoch_reset import reset_public_tracking_once


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
    """Hard-disable legacy exchange FLOW emitters inside the production worker.

    The signal worker imports these callables before ``install_multi_product`` is
    executed. Replacing the module globals here guarantees that stale Matchbook or
    BETDAQ state files cannot generate money-flow Telegram messages after the
    exchange workers have been removed from the supervisor. The daily virtual-bank
    push is disabled as well; the user-facing product is now only Journal/In Game/
    Analysis plus GOOL Brain and autonomous 1xBet STEAM alerts.
    """
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
    """Expose the two-system GOOL product: Brain + autonomous 1xBet STEAM."""
    reset_public_tracking_once()
    install_brain_journal_tracking()
    _disable_exchange_money_runtime()

    # Re-assert the exact three-button public menu. No money/exchange button is
    # appended anywhere in the active product.
    telegram.MENU_KEYBOARD = dict(_CLEAN_MENU_KEYBOARD)
    telegram.report_text = _report_text_clean
    telegram.in_game_sections = _multi_in_game_sections
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
            "🟢 В игре — активные сигналы\n"
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
