from __future__ import annotations

import os
import time
from typing import Any

# Process-local confirmation is intentional: after restart we prefer delaying a win
# rather than restoring a potentially stale provisional goal.
_PROVISIONAL: dict[str, dict[str, Any]] = {}


def _seconds() -> float:
    try:
        return max(12.0, float(os.getenv("VAR_WIN_CONFIRM_SECONDS", "45")))
    except Exception:
        return 45.0


def _observations() -> int:
    try:
        return max(2, int(os.getenv("VAR_WIN_CONFIRM_SNAPSHOTS", "2")))
    except Exception:
        return 2


def _key(row: dict[str, Any]) -> str:
    return "|".join(
        (
            str(row.get("match_id") or ""),
            str(row.get("head") or ""),
            str(row.get("selected_side") or ""),
            str(row.get("created_at") or row.get("minute") or ""),
        )
    )


def clear_provisional(row: dict[str, Any]) -> None:
    _PROVISIONAL.pop(_key(row), None)
    row.pop("provisional_win", None)
    row.pop("provisional_win_score", None)
    row.pop("provisional_win_seen", None)
    row.pop("provisional_win_since", None)


def confirmed_win(
    row: dict[str, Any],
    *,
    raw_won: bool,
    minute: int,
    home_score: int,
    away_score: int,
    now: float | None = None,
) -> bool:
    """Confirm a winning score only after it remains stable across snapshots.

    This prevents a Flashscore goal that is subsequently cancelled by VAR from
    immediately settling a wager as won. A score rollback clears the provisional
    state and the wager remains pending.
    """
    key = _key(row)
    if not raw_won:
        clear_provisional(row)
        return False

    ts = time.time() if now is None else float(now)
    score = [int(home_score), int(away_score)]
    state = _PROVISIONAL.get(key)
    if state is None or list(state.get("score") or []) != score:
        state = {"score": score, "first_seen": ts, "last_seen": ts, "count": 1, "minute": int(minute)}
        _PROVISIONAL[key] = state
    else:
        state["last_seen"] = ts
        state["count"] = int(state.get("count") or 0) + 1
        state["minute"] = int(minute)

    row["provisional_win"] = True
    row["provisional_win_score"] = score
    row["provisional_win_seen"] = int(state.get("count") or 1)
    row["provisional_win_since"] = float(state.get("first_seen") or ts)

    stable_for = ts - float(state.get("first_seen") or ts)
    if int(state.get("count") or 0) >= _observations() and stable_for >= _seconds():
        clear_provisional(row)
        return True
    return False


def provisional_status(row: dict[str, Any]) -> dict[str, Any]:
    state = _PROVISIONAL.get(_key(row)) or {}
    if not state:
        return {"active": False}
    return {
        "active": True,
        "score": list(state.get("score") or []),
        "seen": int(state.get("count") or 0),
        "stable_for_seconds": max(0.0, time.time() - float(state.get("first_seen") or time.time())),
        "required_snapshots": _observations(),
        "required_seconds": _seconds(),
    }
