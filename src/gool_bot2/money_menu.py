from __future__ import annotations

import html
import os
from datetime import datetime, timezone
from typing import Any
from zoneinfo import ZoneInfo

from .matchbook_exchange import load_matchbook_state


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


def _event_volume(event: dict[str, Any]) -> float:
    volume = _number(event.get("volume"))
    if volume > 0.0:
        return volume
    totals = event.get("totals") or {}
    total_volume = sum(
        max(0.0, _number((market or {}).get("volume")))
        for market in totals.values()
        if isinstance(market, dict)
    )
    match_odds = event.get("match_odds") or {}
    return max(total_volume, _number(match_odds.get("volume")))


def _top_1x2(event: dict[str, Any]) -> dict[str, Any] | None:
    market = event.get("match_odds") or {}
    runners = [row for row in market.get("runners") or [] if isinstance(row, dict)]
    if not runners:
        return None
    runner = max(runners, key=lambda row: _number(row.get("volume")))
    volume = max(0.0, _number(runner.get("volume")))
    if volume <= 0.0:
        return None
    return {
        "name": str(runner.get("name") or "?"),
        "volume": volume,
        "market_volume": max(0.0, _number(market.get("volume"))),
    }


def _best_total_flow(event: dict[str, Any]) -> dict[str, Any] | None:
    candidates: list[dict[str, Any]] = []
    for market in (event.get("totals") or {}).values():
        if not isinstance(market, dict):
            continue
        flow = dict(market.get("flow") or {})
        direction = _number(flow.get("direction_pp"))
        activity = max(0.0, _number(flow.get("activity_volume")))
        level = str(flow.get("level") or "NEUTRAL")
        if level not in {"SUPPORT", "STRONG_SUPPORT", "OPPOSITION", "STRONG_OPPOSITION"}:
            continue
        if activity <= 0.0 or abs(direction) < 0.25:
            continue
        period = str(market.get("period") or "FT")
        line = _number(market.get("line"))
        candidates.append(
            {
                "period": period,
                "line": line,
                "direction_pp": direction,
                "activity_volume": activity,
                "level": level,
                "orderbook_confirmed": bool(flow.get("orderbook_confirmed")),
                "score": activity * max(0.25, abs(direction)),
            }
        )
    if not candidates:
        return None
    return max(candidates, key=lambda row: (row["score"], row["activity_volume"]))


def _flow_label(flow: dict[str, Any]) -> str:
    side = "ТБ" if _number(flow.get("direction_pp")) > 0 else "ТМ"
    period = "1Т " if str(flow.get("period") or "FT") == "1H" else ""
    line = _number(flow.get("line"))
    level = str(flow.get("level") or "NEUTRAL")
    strength = "сильный" if level.startswith("STRONG_") else "заметный"
    book = " · стакан подтверждает" if bool(flow.get("orderbook_confirmed")) else ""
    return (
        f"🎯 Поток: <b>{period}{side} {line:g}</b> · {strength} · "
        f"активность {_money(flow.get('activity_volume'))}{book}"
    )


def money_text() -> str:
    state = load_matchbook_state()
    captured = _parse_dt(state.get("captured_at"))
    tz = _tz()
    now = datetime.now(tz)
    today = now.date()
    events: list[dict[str, Any]] = []
    for raw in state.get("events") or []:
        if not isinstance(raw, dict):
            continue
        start = _parse_dt(raw.get("start"))
        if start is None or start.astimezone(tz).date() != today:
            continue
        row = dict(raw)
        row["_start_dt"] = start.astimezone(tz)
        row["_volume"] = _event_volume(row)
        events.append(row)

    events.sort(key=lambda row: (float(row.get("_volume") or 0.0), bool(row.get("in_running"))), reverse=True)
    top = events[:5]
    age_text = ""
    if captured is not None:
        age = max(0, int((datetime.now(timezone.utc) - captured.astimezone(timezone.utc)).total_seconds()))
        age_text = f" · снимок {age}с назад"

    parts = [
        f"💰 <b>GOOL MONEY BOARD · {today.strftime('%d.%m.%Y')}</b>",
        f"Matchbook · реальные биржевые объёмы в GBP{age_text}",
        "<i>Объём ≠ направление. Направление показывается отдельно только когда поток подтверждён движением цены/объёма.</i>",
    ]
    if not top:
        parts.append("На сегодня в текущем Matchbook state нет открытых футбольных матчей с объёмом.")
        return "\n\n".join(parts)

    for index, event in enumerate(top, 1):
        start = event.get("_start_dt")
        status = "🔴 LIVE" if bool(event.get("in_running")) else f"🕒 {start.strftime('%H:%M') if start else '—'}"
        lines = [
            f"<b>{index}. {_h(event.get('home'))} — {_h(event.get('away'))}</b>",
            f"{status} · 💸 объём матча <b>{_money(event.get('_volume'))}</b>",
        ]
        one_x_two = _top_1x2(event)
        if one_x_two is not None:
            lines.append(
                f"🏁 1X2: больше всего проторговано — <b>{_h(one_x_two.get('name'))}</b> "
                f"({_money(one_x_two.get('volume'))})"
            )
        else:
            lines.append("🏁 1X2: нет достаточных данных по объёму")
        flow = _best_total_flow(event)
        if flow is not None:
            lines.append(_flow_label(flow))
        else:
            lines.append("🎯 Поток по тоталам: пока не подтверждён")
        parts.append("\n".join(lines))

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
