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


def _strategy_bucket(strategy: Any) -> str:
    raw = str(strategy or "").strip()
    if raw.startswith("steam_"):
        return "steam"
    return raw


def _latest_settled_minute(
    rows: list[dict[str, Any]],
    match_id: str,
    strategy_bucket: str,
) -> int | None:
    latest: int | None = None
    for row in rows:
        if str(row.get("match_id") or "") != match_id:
            continue
        if _strategy_bucket(row.get("strategy")) != strategy_bucket:
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
    """Block rapid repeat entries only inside the same public GOOL system.

    The first-half and second-half goal systems are independent. A settled
    `goal_before_ht` entry at HT must never delay `another_goal`, which is
    allowed to start immediately at 46'. Autonomous STEAM is also kept in its
    own cooldown bucket. Repeated entries within the same system still observe
    the configured match-minute cooldown.
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

    winner = decision.winner
    strategy_bucket = _strategy_bucket(getattr(winner, "strategy", ""))
    if not strategy_bucket:
        return decision

    latest = _latest_settled_minute(
        load_signal_journal(journal_path),
        match_id,
        strategy_bucket,
    )
    if latest is None:
        return decision

    next_allowed = latest + cooldown
    if minute >= next_allowed:
        return decision

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
        f"WAIT: для системы {strategy_bucket} действует re-entry cooldown {cooldown} мин. "
        f"Предыдущий сигнал этой системы закрыт на {latest}', новый вход не раньше {next_allowed}'."
    )
    return decision
