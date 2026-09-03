from __future__ import annotations

from pathlib import Path

from . import telegram
from .multi_menu import analysis_text, in_game_sections, reconcile_pending, report_text


def install_multi_product() -> None:
    """Make the feature branch menus read the unified Multi journal/analysis.

    This changes reporting and diagnostics only. It does not enable Multi
    Telegram bet emission; the router remains shadow until explicit cutover.
    """
    telegram.report_text = report_text
    telegram.in_game_sections = in_game_sections
    telegram.analysis_text = analysis_text

    def _reconcile(_: Path) -> int:
        return reconcile_pending()

    telegram._force_reconcile_pending = _reconcile
    telegram.START_TEXT = (
        "🟢 <b>GOOL MULTI работает в shadow</b>\n\n"
        "MODEL + PREMATCH + LIVE + 1xBet\n"
        "Один лучший рынок на матч: BEST BET или WAIT.\n\n"
        "📊 Отчёт — статистика выбранных Multi-ставок\n"
        "🟢 В игре — открытые Multi-ставки\n"
        "🧠 Анализ — все текущие Multi-решения\n\n"
        "Боевые Telegram-сигналы пока остаются на старом контуре до завершения shadow-проверки."
    )
