from __future__ import annotations

import html
import os
from datetime import datetime, timezone
from typing import Any
from zoneinfo import ZoneInfo

from .betfair_public_board import load_betfair_state


BUTTON_TEXT = "💰 Деньги"


def _h(value: Any) -> str:
    return html.escape(str(value or ""), quote=False)


def _number(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return float(default)


def _tz():
    try:
        return ZoneInfo(os.getenv("REPORT_TIMEZONE", "Europe/Moscow"))
    except Exception:
        return timezone.utc


def _parse_dt(value: Any) -> datetime | None:
    raw = str(value or "").strip()
    if not raw:
        return None
    try:
        dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except Exception:
        return None


def _money(value: Any) -> str:
    amount = max(0.0, _number(value))
    if amount >= 1_000_000:
        return f"£{amount / 1_000_000:.2f}m"
    if amount >= 1_000:
        return f"£{amount / 1_000:.1f}k"
    return f"£{amount:.0f}"


def _state_age(captured: datetime | None) -> str:
    if captured is None:
        return ""
    age = max(0, int((datetime.now(timezone.utc) - captured.astimezone(timezone.utc)).total_seconds()))
    return f" · снимок {age}с назад"


def _runner_text(event: dict[str, Any]) -> str:
    chunks: list[str] = []
    for row in event.get("runners") or []:
        if not isinstance(row, dict):
            continue
        label = str(row.get("label") or "?")
        back = _number((row.get("best_back") or {}).get("odd"))
        lay = _number((row.get("best_lay") or {}).get("odd"))
        if back > 1.0 and lay > 1.0:
            chunks.append(f"{label} {back:g}/{lay:g}")
        elif back > 1.0:
            chunks.append(f"{label} {back:g}/—")
        elif lay > 1.0:
            chunks.append(f"{label} —/{lay:g}")
    return " · ".join(chunks) or "котировки пока не распознаны"


def _flow_text(event: dict[str, Any]) -> str:
    flow = event.get("flow") or {}
    level = str(flow.get("level") or "WARMING")
    if level == "WARMING" or not bool(flow.get("ready")):
        return "🎯 Направление: прогрев — нужен следующий снимок цены/объёма"
    if level not in {"FLOW", "STRONG_FLOW"}:
        return "🎯 Направление: пока не подтверждено"
    outcome = _h(flow.get("outcome") or "?")
    old_odd = _number(flow.get("old_odd"))
    new_odd = _number(flow.get("new_odd"))
    pp = _number(flow.get("implied_delta_pp"))
    delta = _money(flow.get("matched_delta_gbp"))
    strength = "🔥 сильное" if level == "STRONG_FLOW" else "📈 заметное"
    return (
        f"🎯 Направление: <b>{outcome}</b> · {strength} · "
        f"цена {old_odd:g}→{new_odd:g} · implied {pp:+.1f} п.п. · matched рынка +{delta}"
    )


def _eligible_today(event: dict[str, Any]) -> bool:
    if bool(event.get("in_running")):
        return True
    label = str(event.get("start_label") or "").strip().casefold()
    return label.startswith("today")


def money_text() -> str:
    state = load_betfair_state()
    captured = _parse_dt(state.get("captured_at"))
    today = datetime.now(_tz()).date()
    parts = [
        f"💰 <b>GOOL MONEY BOARD · {today.strftime('%d.%m.%Y')}</b>",
        f"Betfair Exchange PUBLIC · matched в GBP{_state_age(captured)}",
        "<i>Matched — объём всего рынка. Направление П1/X/П2 — наш вывод только из нового matched + движения Back/Lay, а не утверждение, что весь объём поставлен на этот исход.</i>",
    ]

    if not state:
        parts.append("⚠️ Betfair public state ещё не создан. Collector только запускается.")
        return "\n\n".join(parts)

    raw_events = [row for row in (state.get("events") or []) if isinstance(row, dict)]
    if not raw_events:
        statuses = ", ".join(str(x) for x in state.get("statuses") or []) or "нет HTTP-статуса"
        error = str(state.get("error") or "").strip()
        detail = f"\nОшибка: <code>{_h(error)}</code>" if error else ""
        parts.append(
            "⚠️ Публичная Betfair-доска пока не распознана.\n"
            f"HTTP: <b>{_h(statuses)}</b>{detail}\n"
            "Это тест источника без логина; GOOL/STEAM продолжают работать независимо."
        )
        return "\n\n".join(parts)

    events = [dict(row) for row in raw_events if _eligible_today(row)]
    events.sort(key=lambda row: _number(row.get("matched_gbp")), reverse=True)
    top = events[:5]
    if not top:
        labels = ", ".join(str(row.get("start_label") or "?") for row in raw_events[:8])
        parts.append(
            f"Betfair отдал <b>{len(raw_events)}</b> футбольных рынков, но текущий снимок не содержит Today/LIVE.\n"
            f"Ближайшие метки: {_h(labels or '—')}"
        )
        return "\n\n".join(parts)

    for index, event in enumerate(top, 1):
        status = "🔴 LIVE" if bool(event.get("in_running")) else f"🕒 {_h(event.get('start_label') or 'Today')}"
        parts.append(
            f"<b>{index}. {_h(event.get('name') or '?')}</b>\n"
            f"{status} · 💸 matched <b>{_money(event.get('matched_gbp'))}</b>\n"
            f"🏁 Back/Lay: {_h(_runner_text(event))}\n"
            f"{_flow_text(event)}"
        )

    return "\n\n".join(parts)


def install_money_button(telegram_mod: Any) -> None:
    keyboard = getattr(telegram_mod, "MENU_KEYBOARD", None)
    if not isinstance(keyboard, dict):
        return
    rows = keyboard.setdefault("keyboard", [])
    if any(
        str(button.get("text") or "") == BUTTON_TEXT
        for row in rows
        if isinstance(row, list)
        for button in row
        if isinstance(button, dict)
    ):
        return
    if rows and isinstance(rows[-1], list) and len(rows[-1]) == 1:
        rows[-1].append({"text": BUTTON_TEXT})
    else:
        rows.append([{"text": BUTTON_TEXT}])


__all__ = ["BUTTON_TEXT", "install_money_button", "money_text"]
