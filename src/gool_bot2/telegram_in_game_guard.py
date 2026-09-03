from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from . import bot_menu
from . import telegram
from .providers.flashscore import FlashscoreProvider

_ORIG = telegram.in_game_sections


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


def _guarded_in_game_sections(journal_path: Path, analysis_path: Path | None = None) -> list[str]:
    rows = bot_menu._dedupe(bot_menu._load_rows(journal_path))
    pending = [
        r for r in rows
        if str(r.get("result") or "pending").lower() == "pending"
        and str(r.get("head") or "") in bot_menu.MAIN_HEADS
    ]
    if not pending:
        return _ORIG(journal_path, analysis_path)

    ids = {str(r.get("match_id") or "") for r in pending if str(r.get("match_id") or "")}
    states: dict[str, dict[str, Any]] = {}
    status_ok = False
    try:
        provider = FlashscoreProvider()
        states = provider.event_states(ids)
        status_ok = bool(states)
    except Exception as exc:
        print(f"IN_GAME_STATUS_GUARD_ERROR {type(exc).__name__}:{exc}", flush=True)

    analysis_states = bot_menu._latest_live_states(analysis_path)
    allowed_ids: set[str] = set()
    for mid in ids:
        state = states.get(mid)
        if state is not None:
            if bool(state.get("is_live")) and not bool(state.get("is_finished")):
                allowed_ids.add(mid)
            continue
        # If the direct status endpoint returned at least some rows, absence of this
        # match means it is no longer in the current master state set. Do not show a
        # stale journal row as live. If the status lookup failed completely, fall
        # back only to a very fresh analysis snapshot.
        if not status_ok and _fresh_analysis_state(analysis_states.get(mid) or {}):
            allowed_ids.add(mid)

    if allowed_ids == ids:
        return _ORIG(journal_path, analysis_path)

    original_load = bot_menu._load_rows

    def _filtered_load(path):
        loaded = original_load(path)
        if path == journal_path:
            return [
                r for r in loaded
                if str(r.get("result") or "pending").lower() != "pending"
                or str(r.get("head") or "") not in bot_menu.MAIN_HEADS
                or str(r.get("match_id") or "") in allowed_ids
            ]
        return loaded

    bot_menu._load_rows = _filtered_load
    try:
        return _ORIG(journal_path, analysis_path)
    finally:
        bot_menu._load_rows = original_load


telegram.in_game_sections = _guarded_in_game_sections
