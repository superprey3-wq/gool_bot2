from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any

from .journal import load_signal_journal
from .multi_public_metrics import strategy_bucket


def _layer(row: dict[str, Any]) -> str:
    source = str(row.get("signal_source") or row.get("source") or "GOOL")
    return "STEAM" if "STEAM" in source.upper() or str(row.get("source") or "").startswith("1xbet:autonomous_steam") else "GOOL"


def production_report_text(_: Path | None = None, experiment_path: Path | None = None) -> str:
    """Render the canonical public journal without running settlement again.

    Telegram's command handler calls the single production reconcile function
    before invoking this renderer. Keeping the report read-only prevents a second
    hidden settlement/result-delivery pass from racing the LIVE worker.
    """
    del experiment_path
    from . import multi_menu

    rows = load_signal_journal(multi_menu.journal_path())
    tz = multi_menu._tz()
    today = datetime.now(tz).date()
    today_rows = [
        row for row in rows
        if (dt := multi_menu._parse_dt(row.get("created_at"))) is not None and dt.astimezone(tz).date() == today
    ]

    goal_before_ht = [row for row in rows if strategy_bucket(row.get("strategy")) == "goal_before_ht"]
    another_goal = [row for row in rows if strategy_bucket(row.get("strategy")) == "another_goal"]
    steam = [row for row in rows if _layer(row) == "STEAM"]

    parts = [
        "📊 <b>GOOL MULTI · ЖУРНАЛ</b>",
        "Только реально отправленные сигналы. WAIT в статистику не попадает.",
        "",
        f"📅 <b>СЕГОДНЯ · {today.strftime('%d.%m.%Y')}</b>",
        multi_menu._stats_line(today_rows),
        "",
        "📚 <b>ВСЯ НОВАЯ ЭПОХА</b>",
        multi_menu._stats_line(rows),
        "",
        "<b>Две основные системы:</b>",
        f"🟡 Гол в 1-м тайме: {multi_menu._stats_line(goal_before_ht)}",
        f"⚽ Ещё гол: {multi_menu._stats_line(another_goal)}",
        "",
        "<b>Отдельная система прогруза:</b>",
        f"🔥 1xBet STEAM: {multi_menu._stats_line(steam)}",
    ]
    return "\n".join(parts)


__all__ = ["production_report_text"]
