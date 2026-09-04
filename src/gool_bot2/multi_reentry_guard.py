from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from .journal import load_signal_journal


FINAL_RESULTS = {"won", "lost", "push", "void"}


def cooldown_minutes() -> int:
    try:
        return max(0, int(os.getenv("GOOL_MULTI_REENTRY_COOLDOWN_MINUTES", "10")))
    except (TypeError, ValueError):
        return 10


def _latest_settled_minute(rows: list[dict[str, Any]], match_id: str) -> int | None:
    latest: int | None = None
    for row in rows:
        if str(row.get("match_id") or "") != match_id:
            continue
        if str(row.get("result") or "pending").lower() not in FINAL_RESULTS:
            continue
        try:
            minute = int(row.get("settled_minute"))
        except (TypeError, ValueError):
            continue
        if minute < 0:
            continue
        latest = minute if latest is None else max(latest, minute)
    return latest


def enforce_reentry_cooldown(
    decision: Any,
    record: dict[str, Any],
    journal_path: Path,
) -> Any:
    """Block a new public bet on the same match for N match-minutes after settlement.

    The guard is deliberately applied after both GOOL State and autonomous 1xBet
    steam selection, so neither layer can immediately chase the same match after
    a previous entry has just been won/lost/voided.
    """
    if str(getattr(decision, "status", "")) != "BET" or getattr(decision, "winner", None) is None:
        return decision

    cooldown = cooldown_minutes()
    if cooldown <= 0:
        return decision

    match = record.get("match") or {}
    match_id = str(match.get("flashscore_event_id") or "")
    if not match_id:
        return decision
    try:
        minute = int(match.get("minute") or 0)
    except (TypeError, ValueError):
        return decision
    if minute <= 0:
        return decision

    latest = _latest_settled_minute(load_signal_journal(journal_path), match_id)
    if latest is None:
        return decision

    next_allowed = latest + cooldown
    if minute >= next_allowed:
        return decision

    winner = decision.winner
    block = f"reentry_cooldown_{cooldown}m"
    if block not in winner.blocks:
        winner.blocks.append(block)
    if "reentry_cooldown" not in winner.reason_tags:
        winner.reason_tags.append("reentry_cooldown")
    winner.eligible = False
    if not any(row.key == winner.key for row in decision.rejected):
        decision.rejected.append(winner)

    decision.status = "WAIT"
    decision.winner = None
    decision.alternatives = []
    decision.reason = (
        f"WAIT: после предыдущего расчёта по этому матчу действует re-entry cooldown {cooldown} мин. "
        f"Предыдущий сигнал закрыт на {latest}', новый вход не раньше {next_allowed}'."
    )
    return decision
