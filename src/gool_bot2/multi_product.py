from __future__ import annotations

from pathlib import Path

from . import telegram
from .multi_analysis_view import analysis_text as _analysis_text
from .multi_bank import current_bank_summary
from .multi_menu import in_game_sections as _multi_in_game_sections
from .multi_menu import journal_path, reconcile_pending, report_text
from .multi_money_flow import money_flow_open_section, money_flow_report_line
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


def install_multi_product() -> None:
    """Point menus/reporting at the unified Multi product."""
    reset_public_tracking_once()
    telegram.report_text = _report_text_with_bank
    telegram.in_game_sections = _in_game_sections_with_flow
    telegram.analysis_text = _analysis_text_safe

    def _reconcile(_: Path) -> int:
        path = journal_path()
        corrected = reconcile_finalized_first_half(path)
        return corrected + reconcile_pending()

    telegram._force_reconcile_pending = _reconcile
    if is_multi_telegram_active():
        telegram.START_TEXT = (
            "🟢 <b>GOOL MULTI работает</b>\n\n"
            "MODEL + PREMATCH + LIVE + 1xBet + Matchbook\n"
            "Две основные GOOL-системы + отдельные STEAM и MONEY FLOW.\n"
            "Обычный GOOL по-прежнему выбирает один лучший рынок или WAIT.\n\n"
            "📊 Отчёт — GOOL + STEAM + отдельная статистика MONEY FLOW\n"
            "🟢 В игре — открытые BEST BET + MONEY FLOW\n"
            "🧠 Анализ — почему каждый матч BET или WAIT\n"
            "💰 Дневной отчёт банка — автоматически в 23:59"
        )
    else:
        telegram.START_TEXT = (
            "🟢 <b>GOOL MULTI работает в shadow</b>\n\n"
            "MODEL + PREMATCH + LIVE + 1xBet + Matchbook\n"
            "Один лучший рынок на матч: BEST BET или WAIT.\n\n"
            "📊 Отчёт — статистика выбранных Multi-ставок + MONEY FLOW\n"
            "🟢 В игре — открытые Multi-ставки + MONEY FLOW\n"
            "🧠 Анализ — почему каждый матч BET или WAIT\n"
            "💰 Виртуальный банк — дневной отчёт автоматически в 23:59\n\n"
            "Боевые Telegram-сигналы пока остаются на старом контуре до включения GOOL_MULTI_TELEGRAM_MODE=active."
        )