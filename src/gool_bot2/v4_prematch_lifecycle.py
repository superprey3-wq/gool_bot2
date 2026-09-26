from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Iterable

from .providers.common import pair_score


OPEN_RESULTS = {"pending", "tracking"}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def attach_live_state_to_prematch(
    rows: Iterable[dict[str, Any]],
    live_records: Iterable[dict[str, Any]],
    *,
    min_match_score: float = 0.78,
) -> int:
    """Promote prematch bets into the existing in-game lifecycle.

    A prematch row remains the same journal entry. Once a sufficiently matching
    Flashscore game appears it gains match_id/live state instead of creating a
    duplicate LIVE bet.
    """
    records = [dict(r) for r in live_records if isinstance(r, dict)]
    changed = 0
    for row in rows:
        if str(row.get("origin") or "").lower() != "prematch":
            continue
        if str(row.get("result") or "pending").lower() not in OPEN_RESULTS:
            continue
        if row.get("match_id") and str(row.get("lifecycle") or "") == "in_game":
            continue

        best: tuple[float, dict[str, Any]] | None = None
        for record in records:
            match = record.get("match") or record
            score = pair_score(
                str(row.get("home") or ""), str(row.get("away") or ""),
                str(match.get("home") or ""), str(match.get("away") or ""),
            )
            if best is None or score > best[0]:
                best = (score, record)
        if best is None or best[0] < min_match_score:
            continue

        record = best[1]
        match = record.get("match") or record
        match_id = str(match.get("flashscore_event_id") or match.get("match_id") or "").strip()
        if not match_id:
            continue
        minute = int(match.get("minute") or 0)
        started = minute > 0 or bool(match.get("is_halftime")) or bool(match.get("is_finished"))
        if not started:
            continue

        row["match_id"] = match_id
        row["lifecycle"] = "in_game"
        row["live_attached_at"] = _now()
        row["live_match_score"] = round(best[0], 4)
        row["current_minute"] = minute
        row["current_score"] = [
            int(match.get("home_score") or 0),
            int(match.get("away_score") or 0),
        ]
        changed += 1
    return changed


def prematch_in_game_rows(
    rows: Iterable[dict[str, Any]],
    live_records: Iterable[dict[str, Any]],
) -> list[dict[str, Any]]:
    rows = [dict(row) for row in rows]
    attach_live_state_to_prematch(rows, live_records)
    return [
        row for row in rows
        if str(row.get("origin") or "").lower() == "prematch"
        and str(row.get("result") or "pending").lower() in OPEN_RESULTS
        and str(row.get("lifecycle") or "") == "in_game"
    ]
