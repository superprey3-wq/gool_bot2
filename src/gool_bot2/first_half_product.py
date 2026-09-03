from __future__ import annotations

import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from . import bot_menu, local_ensemble, telegram
from .providers.flashscore import FlashscoreProvider

# Make goal_before_ht a first-class strategy in reports/menu views.
bot_menu.HEAD_LABELS["goal_before_ht"] = "🟡 Гол до перерыва"
bot_menu.MAIN_HEADS = ("another_goal", "goal_before_ht", "two_more_goals")
bot_menu.ALL_HEADS = bot_menu.MAIN_HEADS + bot_menu.EXPERIMENT_HEADS
bot_menu.ACTIVE_HEADS = bot_menu.ALL_HEADS

telegram.START_TEXT = (
    "🟢 <b>GOOL Bot 2 работает</b>\n\nАктивные стратегии:\n"
    "⚽ Ещё гол — MODEL + PREMATCH + LIVE\n"
    "🟡 Гол до перерыва — MODEL + LIVE + 1xBet подтверждение\n"
    "🔥 Ещё +2 гола — GOOL LIVE\n"
    "💜 Обе забьют — Да\n"
    "🔵 Команда забьёт\n\n"
    "LIVE-сигналы приходят автоматически.\nЧтобы отключить сигналы: /stop"
)


def _first_half_live_analysis_40(record: dict[str, Any]) -> dict[str, float | None]:
    """GOOL live pressure for one more goal before HT, active through 40'."""
    match = record.get("match") or {}
    minute = float(match.get("minute") or 0.0)
    is_halftime = bool(match.get("is_halftime"))
    output: dict[str, float | None] = {
        "pressure_score": None,
        "probability_adjustment": 0.0,
        "xg_total": local_ensemble._pair_total(record, "xg"),
        "xgot_total": local_ensemble._pair_total(record, "xgot"),
        "shots_total": local_ensemble._pair_total(record, "shots"),
        "sot_total": local_ensemble._pair_total(record, "shots_on_target"),
        "big_chances_total": local_ensemble._pair_total(record, "big_chances"),
        "corners_total": local_ensemble._pair_total(record, "corners"),
        "dangerous_attacks_total": local_ensemble._pair_total(record, "dangerous_attacks"),
        "baseline_auc": local_ensemble.GOAL_BEFORE_HT_BASELINE_AUC,
    }
    max_minute = float(os.getenv("GOAL_BEFORE_HT_MAX_MINUTE", "40"))
    if is_halftime or minute <= 0 or minute > max_minute:
        return output

    progress = max(0.08, min(1.0, minute / 45.0))
    expectations = {
        "xg_total": (1.15 * progress, 0.30),
        "xgot_total": (0.95 * progress, 0.10),
        "shots_total": (12.0 * progress, 0.14),
        "sot_total": (4.0 * progress, 0.20),
        "big_chances_total": (1.8 * progress, 0.12),
        "corners_total": (5.0 * progress, 0.06),
        "dangerous_attacks_total": (48.0 * progress, 0.08),
    }
    pressure, adjustment = local_ensemble._pressure_overlay(output, expectations, 0.08)
    output["pressure_score"] = pressure
    output["probability_adjustment"] = adjustment
    return output


local_ensemble._first_half_live_analysis = _first_half_live_analysis_40


def _fresh_analysis_state(row: dict[str, Any], max_age_minutes: float = 3.0) -> bool:
    raw = str(row.get("captured_at") or "").strip()
    if not raw:
        return False
    try:
        dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        age = (datetime.now(timezone.utc) - dt.astimezone(timezone.utc)).total_seconds() / 60.0
        minute = int(row.get("minute") or 0)
    except Exception:
        return False
    return -1.0 <= age <= max_age_minutes and 0 < minute < 90


def _render_in_game(journal_path: Path, analysis_path: Path | None = None) -> list[str]:
    rows = bot_menu._dedupe(bot_menu._load_rows(journal_path))
    states_from_analysis = bot_menu._latest_live_states(analysis_path)
    pending = [
        r for r in rows
        if str(r.get("result") or "pending").lower() == "pending"
        and str(r.get("head") or "") in bot_menu.MAIN_HEADS
    ]
    ids = {str(r.get("match_id") or "") for r in pending if str(r.get("match_id") or "")}

    live_states: dict[str, dict[str, Any]] = {}
    status_ok = False
    if ids:
        try:
            live_states = FlashscoreProvider().event_states(ids)
            status_ok = bool(live_states)
        except Exception as exc:
            print(f"FIRST_HALF_MENU_STATUS_ERROR {type(exc).__name__}:{exc}", flush=True)

    allowed: list[dict[str, Any]] = []
    for row in pending:
        mid = str(row.get("match_id") or "")
        state = live_states.get(mid)
        if state is not None:
            if bool(state.get("is_live")) and not bool(state.get("is_finished")):
                allowed.append(row)
            continue
        if not status_ok and _fresh_analysis_state(states_from_analysis.get(mid) or {}):
            allowed.append(row)

    allowed.sort(key=lambda r: str(r.get("created_at") or ""), reverse=True)
    if not allowed:
        return ["🟢 <b>В ИГРЕ</b>\n\nАктивных сигналов сейчас нет."]

    groups = (
        ("another_goal", "⚽ <b>ЕЩЁ ГОЛ</b>"),
        ("goal_before_ht", "🟡 <b>ГОЛ ДО ПЕРЕРЫВА</b>"),
        ("two_more_goals", "🔥 <b>ЕЩЁ +2 ГОЛА</b>"),
    )
    messages = [f"🟢 <b>В ИГРЕ</b>\nАктивных сигналов: <b>{len(allowed)}</b>"]
    for head, title in groups:
        selected = [r for r in allowed if str(r.get("head") or "") == head]
        if not selected:
            continue
        parts = [f"{title} · <b>{len(selected)}</b>"] + [
            f"<b>{i}.</b> {bot_menu._in_game_row(r, states_from_analysis)}"
            for i, r in enumerate(selected, 1)
        ]
        chunk = parts[0]
        for part in parts[1:]:
            candidate = chunk + "\n\n" + part
            if len(candidate) > 3800:
                messages.append(chunk)
                chunk = title + " · продолжение\n\n" + part
            else:
                chunk = candidate
        messages.append(chunk)
    return messages


telegram.in_game_sections = _render_in_game

_orig_analysis_text = telegram.analysis_text

def _analysis_text_with_first_half(*args, **kwargs):
    text = _orig_analysis_text(*args, **kwargs)
    return text.replace("4 СТРАТЕГИИ", "5 СТРАТЕГИЙ")

telegram.analysis_text = _analysis_text_with_first_half
