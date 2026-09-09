from __future__ import annotations

import html
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from .betdaq_selection_lifecycle import load_signals


_INSTALLED = False
_ORIGINAL_REPORT: Callable[..., str] | None = None
_ORIGINAL_IN_GAME: Callable[..., list[str]] | None = None


def _parse_dt(value: Any) -> datetime | None:
    raw = str(value or "").strip()
    if not raw:
        return None
    try:
        dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except Exception:
        return None


def _pct(won: int, lost: int) -> str:
    total = won + lost
    return "—" if total <= 0 else f"{won / total * 100:.1f}%"


def _stats(rows: list[dict[str, Any]]) -> str:
    won = sum(1 for row in rows if str(row.get("result") or "") == "won")
    lost = sum(1 for row in rows if str(row.get("result") or "") == "lost")
    pending = sum(1 for row in rows if str(row.get("result") or "pending") == "pending")
    void = sum(1 for row in rows if str(row.get("result") or "") == "void")
    tail = f" · ↔️ {void}" if void else ""
    return f"✅ {won} · ❌ {lost} · ⏳ {pending}{tail} · проход <b>{_pct(won, lost)}</b>"


def report_with_betdaq(path: Path, experiment_path: Path | None = None) -> str:
    base = _ORIGINAL_REPORT(path, experiment_path) if _ORIGINAL_REPORT is not None else ""
    rows = load_signals()
    if not rows:
        return base
    try:
        from .bot_menu import _tz

        tz = _tz()
    except Exception:
        tz = timezone.utc
    today = datetime.now(tz).date()
    today_rows = []
    for row in rows:
        dt = _parse_dt(row.get("created_at"))
        if dt is not None and dt.astimezone(tz).date() == today:
            today_rows.append(row)
    section = (
        "💰 <b>BETDAQ ПРОГРУЗ</b>\n"
        f"Сегодня: {_stats(today_rows)}\n"
        f"За всё время: {_stats(rows)}"
    )
    return (base + "\n\n────────────\n\n" + section).strip()


def _money(value: Any) -> str:
    try:
        amount = float(value or 0.0)
    except (TypeError, ValueError):
        amount = 0.0
    if amount >= 1000:
        return f"£{amount / 1000:.1f}k"
    return f"£{amount:.0f}"


def _pending_section(rows: list[dict[str, Any]]) -> str:
    parts = [f"💰 <b>BETDAQ ПРОГРУЗ · ОТКРЫТЫ · {len(rows)}</b>"]
    for index, row in enumerate(rows[:20], 1):
        live = row.get("last_live_score") or row.get("entry_live_score") or []
        score_text = ""
        if isinstance(live, (list, tuple)) and len(live) >= 2:
            score_text = f" · сейчас {int(live[0] or 0)}:{int(live[1] or 0)}"
        mapped = "" if row.get("flashscore_event_id") else " · ⚠️ матч ещё не сопоставлен"
        parts.append(
            f"<b>{index}.</b> {html.escape(str(row.get('event_name') or '?'))}\n"
            f"🎯 <b>{html.escape(str(row.get('label') or '?'))}</b>{score_text}\n"
            f"💷 +{_money(row.get('delta_for_gbp'))} за {html.escape(str(row.get('window') or '?'))} · "
            f"score {float(row.get('score') or 0):.0f}/100{mapped}"
        )
    if len(rows) > 20:
        parts.append(f"… ещё {len(rows) - 20}")
    return "\n\n".join(parts)


def in_game_with_betdaq(journal_path: Path, analysis_path: Path | None = None) -> list[str]:
    base = list(_ORIGINAL_IN_GAME(journal_path, analysis_path)) if _ORIGINAL_IN_GAME is not None else []
    pending = [row for row in load_signals() if str(row.get("result") or "pending") == "pending"]
    pending.sort(key=lambda row: str(row.get("created_at") or ""), reverse=True)
    if not pending:
        return base
    base.append(_pending_section(pending))
    return base


def install_betdaq_signal_menu_patch() -> None:
    global _INSTALLED, _ORIGINAL_REPORT, _ORIGINAL_IN_GAME
    if _INSTALLED:
        return
    from . import telegram

    _ORIGINAL_REPORT = telegram.report_text
    _ORIGINAL_IN_GAME = telegram.in_game_sections
    telegram.report_text = report_with_betdaq
    telegram.in_game_sections = in_game_with_betdaq
    _INSTALLED = True
    print("BETDAQ_SELECTION_MENU installed report=on in_game=on", flush=True)


__all__ = ["install_betdaq_signal_menu_patch", "in_game_with_betdaq", "report_with_betdaq"]
