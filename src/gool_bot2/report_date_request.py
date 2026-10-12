"""Parse an optional Moscow-local date for Telegram /dayreport and /fullreport."""
from __future__ import annotations

from datetime import date, datetime, timedelta
from .full_report_export import _tz


def requested_report_date(raw_text: str) -> date | None:
    """None means today's default report; ValueError means malformed input.

    Examples: /dayreport, /dayreport 2026-10-12, /dayreport 12.10.2026,
    /dayreport вчера, /dayreport@MyBot 2026-10-12.
    """
    pieces = str(raw_text or "").strip().split()
    if not pieces:
        raise ValueError("Пустая команда. Пример: /dayreport 2026-10-12")
    command = pieces[0].split("@", 1)[0].casefold()
    if command not in {"/dayreport", "/fullreport"}:
        raise ValueError("Неизвестная команда отчёта.")
    if len(pieces) == 1:
        return None
    if len(pieces) != 2:
        raise ValueError("Укажи одну дату: /dayreport 2026-10-12")
    raw = pieces[1].strip().casefold()
    today = datetime.now(_tz()).date()
    if raw in {"вчера", "yesterday"}:
        return today - timedelta(days=1)
    if raw in {"сегодня", "today"}:
        return today
    try:
        if len(raw) == 10 and raw[4] == "-" and raw[7] == "-":
            selected = date.fromisoformat(raw)
        else:
            selected = datetime.strptime(raw, "%d.%m.%Y").date()
    except (ValueError, TypeError) as exc:
        raise ValueError("Неверная дата. Пример: /dayreport 2026-10-12 или /dayreport 12.10.2026") from exc
    if selected > today:
        raise ValueError("Отчёт за будущую дату недоступен.")
    return selected
