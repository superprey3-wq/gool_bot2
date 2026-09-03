from __future__ import annotations

from pathlib import Path

from . import telegram
from .multi_analysis_view import analysis_text
from .multi_bank import current_bank_summary
from .multi_menu import in_game_sections, journal_path, reconcile_pending, report_text
from .multi_telegram import is_multi_telegram_active


def _report_text_with_bank(*args, **kwargs) -> str:
    text = report_text(*args, **kwargs)
    bank = "\n".join(current_bank_summary(journal_path()))
    return f"{text}\n\n{bank}"


def install_multi_product() -> None:
    """Point menus/reporting at the unified Multi product."""
    telegram.report_text = _report_text_with_bank
    telegram.in_game_sections = in_game_sections
    telegram.analysis_text = analysis_text

    def _reconcile(_: Path) -> int:
        return reconcile_pending()

    telegram._force_reconcile_pending = _reconcile
    if is_multi_telegram_active():
        telegram.START_TEXT = (
            "🟢 <b>GOOL MULTI работает</b>\n\n"
            "MODEL + PREMATCH + LIVE + 1xBet\n"
            "Один лучший рынок на матч: BEST BET или WAIT.\n"
            "Сигнал и результат приходят одной Multi-карточкой с эмблемами и LIVE-статистикой.\n\n"
            "📊 Отчёт — статистика Multi + виртуальный банк\n"
            "🟢 В игре — открытые BEST BET\n"
            "🧠 Анализ — почему каждый матч BET или WAIT\n"
            "💰 Дневной отчёт банка — автоматически в 23:59"
        )
    else:
        telegram.START_TEXT = (
            "🟢 <b>GOOL MULTI работает в shadow</b>\n\n"
            "MODEL + PREMATCH + LIVE + 1xBet\n"
            "Один лучший рынок на матч: BEST BET или WAIT.\n\n"
            "📊 Отчёт — статистика выбранных Multi-ставок + виртуальный банк\n"
            "🟢 В игре — открытые Multi-ставки\n"
            "🧠 Анализ — почему каждый матч BET или WAIT\n"
            "💰 Виртуальный банк — дневной отчёт автоматически в 23:59\n\n"
            "Боевые Telegram-сигналы пока остаются на старом контуре до включения GOOL_MULTI_TELEGRAM_MODE=active."
        )
