from __future__ import annotations

import html
import os
from datetime import datetime, timezone
from typing import Any
from zoneinfo import ZoneInfo

from .betdaq_exchange import load_betdaq_state


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


def _usdc(value: Any) -> str:
    amount = max(0.0, _number(value))
    if amount >= 1_000_000:
        return f"${amount / 1_000_000:.2f}m"
    if amount >= 1_000:
        return f"${amount / 1_000:.1f}k"
    return f"${amount:.0f}"


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
        label = str(row.get("label") or row.get("name") or "?")
        back_row = row.get("best_back") or {}
        lay_row = row.get("best_lay") or {}
        back = _number(back_row.get("odd", back_row.get("odds")))
        lay = _number(lay_row.get("odd", lay_row.get("odds")))
        if back > 1.0 and lay > 1.0:
            chunks.append(f"{label} {back:g}/{lay:g}")
        elif back > 1.0:
            chunks.append(f"{label} {back:g}/—")
        elif lay > 1.0:
            chunks.append(f"{label} —/{lay:g}")
    return " · ".join(chunks) or "котировки пока прогреваются"


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
    price = f"цена {old_odd:g}→{new_odd:g}" if old_odd > 1.0 and new_odd > 1.0 else "цена движется"
    return (
        f"🎯 Направление: <b>{outcome}</b> · {strength} · "
        f"{price} · implied {pp:+.1f} п.п. · новый matched рынка +{delta}"
    )


def _eligible_today(event: dict[str, Any]) -> bool:
    if bool(event.get("in_running")):
        return True
    start = _parse_dt(event.get("start"))
    return bool(start is not None and start.astimezone(_tz()).date() == datetime.now(_tz()).date())


def _start_label(event: dict[str, Any]) -> str:
    if bool(event.get("in_running")):
        return "🔴 LIVE"
    start = _parse_dt(event.get("start"))
    if start is None:
        return "🕒 сегодня"
    return f"🕒 {start.astimezone(_tz()).strftime('%H:%M')}"


def _coverage_text(state: dict[str, Any]) -> str:
    events = int(_number(state.get("tracked_events")))
    markets = int(_number(state.get("tracked_markets")))
    totals = int(_number(state.get("tracked_total_markets")))
    total_events = int(_number(state.get("events_with_totals")))
    base = f"📡 Поле: <b>{events}</b> матчей · <b>{markets}</b> рынков под наблюдением"
    if totals > 0 or total_events > 0:
        base += f"\n⚽ GOOL totals: <b>{totals}</b> рынков · <b>{total_events}</b> матчей с тоталами"
    return base


def _sxbet_quote(event: dict[str, Any], label: str) -> str:
    row = (event.get("outcomes") or {}).get(label) or {}
    odd = _number(row.get("decimal_odd"))
    depth = _number(row.get("available_usdc"))
    if odd <= 1.0:
        return f"{label} —"
    return f"{label} {odd:.2f} · {_usdc(depth)}"


def _sxbet_flow_text(event: dict[str, Any], *, prefix: str = "SX направление") -> str:
    flow = event.get("flow") or {}
    level = str(flow.get("level") or "WARMING")
    if level == "WARMING" or not bool(flow.get("ready")):
        return f"🎯 {prefix}: прогрев — сравним следующий снимок цены и стакана"
    if level not in {"LIQUIDITY_PUSH", "STRONG_LIQUIDITY_PUSH"}:
        return f"🎯 {prefix}: подтверждённого давления стакана пока нет"
    outcome = _h(flow.get("outcome") or "?")
    old_odd = _number(flow.get("old_odd"))
    new_odd = _number(flow.get("new_odd"))
    pp = _number(flow.get("implied_delta_pp"))
    depth_delta = _number(flow.get("liquidity_delta_usdc"))
    rel = _number(flow.get("relative_liquidity_pct"))
    strength = "🔥 сильное" if level == "STRONG_LIQUIDITY_PUSH" else "📈 заметное"
    return (
        f"🎯 {prefix}: <b>{outcome}</b> · {strength} LIQUIDITY PUSH · "
        f"цена {old_odd:.2f}→{new_odd:.2f} · implied {pp:+.1f} п.п. · "
        f"стакан +{_usdc(depth_delta)} ({rel:+.1f}%)"
    )


def _sxbet_totals_text() -> str:
    try:
        from .sxbet_totals import fetch_sxbet_totals_state

        state = fetch_sxbet_totals_state()
    except Exception as exc:
        return (
            "⚽ <b>SX TOTALS</b>\n"
            f"⚠️ Тоталы не запустились: <code>{_h(type(exc).__name__ + ':' + str(exc))}</code>"
        )

    captured = _parse_dt(state.get("captured_at"))
    parts = [f"⚽ <b>SX TOTALS · ТБ/ТМ</b> · main LIVE line{_state_age(captured)}"]
    if not bool(state.get("available")):
        parts.append(f"⚠️ Причина: <code>{_h(state.get('error') or 'sxbet_totals_unavailable')}</code>")
        return "\n".join(parts)

    events = [dict(row) for row in (state.get("events") or []) if isinstance(row, dict)]
    events.sort(key=lambda row: _number(row.get("liquidity_usdc")), reverse=True)
    top = events[:5]
    if not top:
        parts.append(
            "SX Bet отвечает, но основных LIVE Under/Over рынков со стаканом сейчас не найдено. "
            f"Рынков: {int(_number(state.get('markets_seen')))}"
        )
        return "\n".join(parts)

    for index, event in enumerate(top, 1):
        line = _number(event.get("line"))
        parts.append(
            f"<b>{index}. {_h(event.get('name') or '?')}</b>\n"
            f"📏 Тотал <b>{line:g}</b> · {_sxbet_quote(event, 'TB')} · {_sxbet_quote(event, 'TM')}\n"
            f"💵 глубина <b>{_usdc(event.get('liquidity_usdc'))}</b>\n"
            f"{_sxbet_flow_text(event, prefix='SX тотал')}"
        )
    return "\n\n".join(parts)


def _sxbet_fallback_text() -> str:
    try:
        from .sxbet_public import fetch_sxbet_state

        state = fetch_sxbet_state()
    except Exception as exc:
        state = {
            "available": False,
            "error": f"{type(exc).__name__}:{exc}",
            "events": [],
        }

    captured = _parse_dt(state.get("captured_at"))
    parts = [
        f"🟦 <b>SX BET RESERVE</b> · anonymous REST · USDC{_state_age(captured)}",
        (
            "<i>Здесь показывается реальная исполнимая ликвидность SX-стакана, "
            "а не matched/traded volume. Поэтому GOOL не смешивает её с BETDAQ matched.</i>"
        ),
    ]

    if not bool(state.get("available")):
        parts.append(
            "⚠️ SX Bet 1X2 REST сейчас недоступен.\n"
            f"Причина: <code>{_h(state.get('error') or 'sxbet_unavailable')}</code>"
        )
    else:
        events = [dict(row) for row in (state.get("events") or []) if isinstance(row, dict)]
        events.sort(key=lambda row: _number(row.get("liquidity_usdc")), reverse=True)
        top = events[:5]
        if not top:
            parts.append(
                "✅ SX Bet public REST отвечает, но LIVE-футбольных 1X2 рынков со стаканом сейчас не найдено.\n"
                f"Получено рынков: <b>{int(_number(state.get('markets_seen')))}</b> · "
                f"заявок: <b>{int(_number(state.get('orders_seen')))}</b>"
            )
        else:
            for index, event in enumerate(top, 1):
                parts.append(
                    f"<b>{index}. {_h(event.get('name') or '?')}</b>\n"
                    f"🔴 LIVE · 💵 исполнимая глубина <b>{_usdc(event.get('liquidity_usdc'))}</b>\n"
                    f"🏁 {_sxbet_quote(event, 'P1')} · {_sxbet_quote(event, 'X')} · {_sxbet_quote(event, 'P2')}\n"
                    f"{_sxbet_flow_text(event)}"
                )
            parts.append(
                f"📡 SX: <b>{len(events)}</b> LIVE матчей · "
                f"<b>{int(_number(state.get('markets_seen')))}</b> рынков · "
                f"<b>{int(_number(state.get('orders_seen')))}</b> заявок"
            )

    parts.append(_sxbet_totals_text())
    return "\n\n".join(parts)


def money_text() -> str:
    state = load_betdaq_state()
    captured = _parse_dt(state.get("captured_at"))
    today = datetime.now(_tz()).date()
    parts = [
        f"💰 <b>GOOL MONEY BOARD · {today.strftime('%d.%m.%Y')}</b>",
        f"BETDAQ Exchange · anonymous AAPI · matched в GBP{_state_age(captured)}",
        "<i>Matched — реальный объём рынка. Направление П1/X/П2 показываем только когда новый matched совпал с движением Back/Lay; весь объём не приписывается одному исходу.</i>",
    ]

    if not state:
        parts.append("⚠️ BETDAQ state ещё не создан. Биржевой collector только запускается.")
        parts.append(_sxbet_fallback_text())
        return "\n\n".join(parts)

    if not bool(state.get("available", True)):
        error = str(state.get("error") or "BETDAQ stream unavailable").strip()
        parts.append(
            "⚠️ BETDAQ stream сейчас недоступен.\n"
            f"Причина: <code>{_h(error)}</code>\n"
            "GOOL Brain и 1xBet STEAM продолжают работать независимо."
        )
        parts.append(_sxbet_fallback_text())
        return "\n\n".join(parts)

    raw_events = [row for row in (state.get("events") or []) if isinstance(row, dict)]
    events = [dict(row) for row in raw_events if _eligible_today(row)]
    events.sort(key=lambda row: _number(row.get("matched_gbp")), reverse=True)
    top = [row for row in events if _number(row.get("matched_gbp")) > 0.0][:5]
    if not top:
        parts.append(
            f"BETDAQ видит <b>{len(events)}</b> футбольных матчей на сегодня, но Match Odds matched пока не прогрузился."
        )
        parts.append(_coverage_text(state))
        parts.append(_sxbet_fallback_text())
        return "\n\n".join(parts)

    for index, event in enumerate(top, 1):
        parts.append(
            f"<b>{index}. {_h(event.get('name') or '?')}</b>\n"
            f"{_start_label(event)} · 💸 Match Odds matched <b>{_money(event.get('matched_gbp'))}</b>\n"
            f"🏁 Back/Lay: {_h(_runner_text(event))}\n"
            f"{_flow_text(event)}"
        )

    parts.append(_coverage_text(state))
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
