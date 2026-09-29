from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any

from .journal import load_signal_journal
from .multi_delivery import was_publicly_sent
from .multi_public_metrics import strategy_bucket


def _layer(row: dict[str, Any]) -> str:
    source = str(row.get("signal_source") or row.get("source") or "GOOL")
    return "STEAM" if "STEAM" in source.upper() or str(row.get("source") or "").startswith("1xbet:autonomous_steam") else "GOOL"


def _today(rows: list[dict[str, Any]], tz: Any, day: Any) -> list[dict[str, Any]]:
    from . import multi_menu
    out = []
    for row in rows:
        dt = multi_menu._parse_dt(row.get("created_at"))
        if dt is not None and dt.astimezone(tz).date() == day:
            out.append(row)
    return out


def production_report_text(_: Path | None = None, experiment_path: Path | None = None) -> str:
    """GOOL Bot 4 public journal: only entries that were actually sent to Telegram."""
    del experiment_path
    from . import multi_menu

    all_rows = load_signal_journal(multi_menu.journal_path())
    rows = [row for row in all_rows if was_publicly_sent(row)]
    tz = multi_menu._tz()
    today = datetime.now(tz).date()
    today_rows = _today(rows, tz, today)

    first_half = [row for row in today_rows if strategy_bucket(row.get("strategy")) == "goal_before_ht"]
    another_goal = [row for row in today_rows if strategy_bucket(row.get("strategy")) == "another_goal"]
    prematch_singles = [row for row in today_rows if str(row.get("origin") or "") == "prematch"]
    parlays = [row for row in today_rows if str(row.get("origin") or "") == "prematch_parlay"]
    steam = [row for row in today_rows if _layer(row) == "STEAM"]

    parts = [
        "📊 <b>GOOL BOT 4 · ОТЧЁТ СЕГОДНЯ</b>",
        f"📅 {today.strftime('%d.%m.%Y')} · только реально отправленные ставки",
        "",
        f"🟡 <b>Гол в 1-м тайме</b>\n{multi_menu._stats_line(first_half)}",
        "",
        f"⚽ <b>Ещё гол</b>\n{multi_menu._stats_line(another_goal)}",
        "",
        f"🎟 <b>PREMATCH ординары</b>\n{multi_menu._stats_line(prematch_singles)}",
        "",
        f"🔗 <b>Экспрессы</b>\n{multi_menu._stats_line(parlays)}",
        "",
        f"🔥 <b>Прогрузы 1xBet</b>\n{multi_menu._stats_line(steam)}",
        "",
        f"📦 Всего отправлено сегодня: <b>{len(today_rows)}</b>",
    ]
    return "\n".join(parts)


__all__ = ["production_report_text"]
