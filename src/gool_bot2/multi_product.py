from __future__ import annotations

import sys
from pathlib import Path

from . import telegram
from .betfair_public_http_worker import start_background_worker as start_betfair_public_worker
from .money_menu import install_money_button, money_text
from .multi_analysis_view import analysis_text as _analysis_text
from .multi_bank import current_bank_summary
from .multi_menu import in_game_sections as _multi_in_game_sections
from .multi_menu import journal_path, reconcile_pending, report_text
from .multi_money_flow import money_flow_open_section, money_flow_report_line
from .multi_money_flow_total_volume import install_money_flow_total_volume
from .multi_result_reconcile import reconcile_finalized_first_half
from .multi_telegram import is_multi_telegram_active
from .public_epoch_reset import reset_public_tracking_once


def _report_text_with_bank(*args, **kwargs) -> str:
    path = journal_path()
    reconcile_finalized_first_half(path)
    text = report_text(*args, **kwargs)
    flow = money_flow_report_line()
    bank = "\n".join(current_bank_summary(path))
    return (
        f"{text}\n\n"
        "<b>Отдельная система денежного потока:</b>\n"
        f"{flow}\n\n"
        f"{bank}"
    )


def _in_game_sections_with_flow(*args, **kwargs) -> list[str]:
    sections = list(_multi_in_game_sections(*args, **kwargs))
    flow = money_flow_open_section()
    if flow:
        sections.append(flow)
    return sections


def _analysis_text_safe(*args, **kwargs) -> str:
    """Keep diagnostic comparison signs from being parsed as Telegram HTML tags."""
    text = _analysis_text(*args, **kwargs)
    return text.replace("PRICE<", "PRICE&lt;").replace("RATING<", "RATING&lt;")


def _install_direct_money_handler() -> bool:
    """Extend the existing single background responder; never start another poller."""
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
        if bool(getattr(module, "_GOOL_MONEY_MENU_INSTALLED", False)):
            return True
        original = getattr(module, "_handle_direct_telegram_update", None)
        direct_send = getattr(module, "_direct_send_message", None)
        if not callable(original) or not callable(direct_send):
            continue

        def money_aware_handler(token, journal_path_arg, update, _original=original, _send=direct_send):
            message = update.get("message") or {}
            raw_text = str(message.get("text") or "").strip()
            text = raw_text.split("@", 1)[0].lower()
            chat_id = (message.get("chat") or {}).get("id")
            if chat_id is not None and text == "💰 деньги":
                try:
                    reply = money_text()
                except Exception as exc:
                    print(
                        f"GOOL_TELEGRAM_MONEY_ERROR {type(exc).__name__}:{exc}",
                        flush=True,
                    )
                    reply = "⚠️ <b>ДЕНЬГИ</b>\n\nНе удалось прочитать свежую публичную Betfair Exchange доску. Попробуй ещё раз чуть позже."
                return int(_send(token, chat_id, reply, reply_markup=telegram.MENU_KEYBOARD))
            return _original(token, journal_path_arg, update)

        setattr(module, "_handle_direct_telegram_update", money_aware_handler)
        setattr(module, "_GOOL_MONEY_MENU_INSTALLED", True)
        return True
    return False


def install_multi_product() -> None:
    """Point menus/reporting at the unified Multi product."""
    install_money_flow_total_volume()
    reset_public_tracking_once()
    telegram.report_text = _report_text_with_bank
    telegram.in_game_sections = _in_game_sections_with_flow
    telegram.analysis_text = _analysis_text_safe
    install_money_button(telegram)
    _install_direct_money_handler()
    # Public Betfair board is a read-only trial source and runs inside this worker
    # process as one daemon thread. It does not create a second Telegram poller or
    # a second Chromium process.
    start_betfair_public_worker()

    def _reconcile(_: Path) -> int:
        path = journal_path()
        corrected = reconcile_finalized_first_half(path)
        return corrected + reconcile_pending()

    telegram._force_reconcile_pending = _reconcile
    if is_multi_telegram_active():
        telegram.START_TEXT = (
            "🟢 <b>GOOL MULTI работает</b>\n\n"
            "MODEL + PREMATCH + LIVE + 1xBet + Betfair PUBLIC\n"
            "Две основные GOOL-системы + отдельный STEAM. Betfair MONEY FLOW пока проходит production-проверку.\n"
            "Обычный GOOL по-прежнему выбирает один лучший рынок или WAIT.\n\n"
            "📊 Отчёт — GOOL + STEAM + статистика MONEY FLOW\n"
            "🟢 В игре — открытые BEST BET + MONEY FLOW\n"
            "🧠 Анализ — почему каждый матч BET или WAIT\n"
            "💰 Деньги — топ-5 матчей по публичному Betfair matched + движение 1X2\n"
            "💰 Дневной отчёт банка — автоматически в 23:59"
        )
    else:
        telegram.START_TEXT = (
            "🟢 <b>GOOL MULTI работает в shadow</b>\n\n"
            "MODEL + PREMATCH + LIVE + 1xBet + Betfair PUBLIC\n"
            "Один лучший рынок на матч: BEST BET или WAIT.\n\n"
            "📊 Отчёт — статистика выбранных Multi-ставок + MONEY FLOW\n"
            "🟢 В игре — открытые Multi-ставки + MONEY FLOW\n"
            "🧠 Анализ — почему каждый матч BET или WAIT\n"
            "💰 Деньги — топ-5 матчей по публичному Betfair matched + движение 1X2\n"
            "💰 Виртуальный банк — дневной отчёт автоматически в 23:59\n\n"
            "Боевые Telegram-сигналы пока остаются на старом контуре до включения GOOL_MULTI_TELEGRAM_MODE=active."
        )
