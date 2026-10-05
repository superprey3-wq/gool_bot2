from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any

from .journal import load_signal_journal
from .multi_delivery import was_publicly_sent
from .multi_public_metrics import strategy_bucket
from .multisport_journal import load_journal as load_multisport_journal


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
    """Unified public journal for football + hockey + basketball."""
    del experiment_path
    from . import multi_menu
    from . import multisport_menu

    tz = multi_menu._tz()
    today = datetime.now(tz).date()

    football_all = [
        row for row in load_signal_journal(multi_menu.journal_path())
        if was_publicly_sent(row) and not bool(row.get("public_duplicate"))
    ]
    football_today = _today(football_all, tz, today)

    multi_all_raw = load_multisport_journal(multisport_menu.journal_path())
    # Multisport journal may contain shadow/audit rows. The public combined
    # journal must mirror what the user actually received in Telegram.
    multi_all = [
        row for row in multi_all_raw
        if bool(row.get("telegram_sent"))
    ]
    multi_today = _today(multi_all, tz, today)

    hockey_all = [row for row in multi_all if str(row.get("sport") or "") == "hockey"]
    basketball_all = [row for row in multi_all if str(row.get("sport") or "") == "basketball"]
    hockey_today = [row for row in multi_today if str(row.get("sport") or "") == "hockey"]
    basketball_today = [row for row in multi_today if str(row.get("sport") or "") == "basketball"]

    today_all = [*football_today, *hockey_today, *basketball_today]
    lifetime_all = [*football_all, *hockey_all, *basketball_all]

    first_half = [row for row in football_today if strategy_bucket(row.get("strategy")) == "goal_before_ht"]
    another_goal = [row for row in football_today if strategy_bucket(row.get("strategy")) == "another_goal"]
    prematch_singles = [row for row in football_today if str(row.get("origin") or "") == "prematch"]
    prematch_values = [row for row in football_today if str(row.get("origin") or "") == "prematch_value"]
    football_parlays = [row for row in football_today if str(row.get("origin") or "") == "prematch_parlay"]

    parts = [
        "📊 <b>GOOL BOT 4 · ОБЩИЙ ЖУРНАЛ</b>",
        f"📅 <b>{today.strftime('%d.%m.%Y')}</b> · только реально отправленные ставки",
        "",
        "━━━━━━━━━━━━━━",
        "📅 <b>СЕГОДНЯ · ВСЕ ВИДЫ СПОРТА</b>",
        f"🌐 <b>ОБЩИЙ</b>\n{multi_menu._stats_line(today_all)}",
        "",
        f"⚽ <b>ФУТБОЛ</b>\n{multi_menu._stats_line(football_today)}",
        f"🏒 <b>ХОККЕЙ</b>\n{multi_menu._stats_line(hockey_today)}",
        f"🏀 <b>БАСКЕТБОЛ</b>\n{multi_menu._stats_line(basketball_today)}",
        "",
        "━━━━━━━━━━━━━━",
        "📚 <b>ЗА ВСЁ ВРЕМЯ</b>",
        f"🌐 <b>ОБЩИЙ</b>\n{multi_menu._stats_line(lifetime_all)}",
        f"⚽ <b>ФУТБОЛ</b>\n{multi_menu._stats_line(football_all)}",
        f"🏒 <b>ХОККЕЙ</b>\n{multi_menu._stats_line(hockey_all)}",
        f"🏀 <b>БАСКЕТБОЛ</b>\n{multi_menu._stats_line(basketball_all)}",
        "",
        "━━━━━━━━━━━━━━",
        "⚽ <b>ФУТБОЛ · ДЕТАЛИ СЕГОДНЯ</b>",
        f"🟡 Гол в 1-м тайме\n{multi_menu._stats_line(first_half)}",
        f"⚽ Ещё гол\n{multi_menu._stats_line(another_goal)}",
        f"🎟 PREMATCH ординары\n{multi_menu._stats_line(prematch_singles)}",
        f"🔥 VALUE HUNTER\n{multi_menu._stats_line(prematch_values)}",
        f"🔗 Экспрессы\n{multi_menu._stats_line(football_parlays)}",
    ]
    return "\n\n".join(parts)


__all__ = ["production_report_text"]
