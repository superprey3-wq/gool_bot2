from __future__ import annotations

import os
from typing import Any


def _minute(value: Any) -> int | None:
    try:
        minute = int(value)
    except (TypeError, ValueError):
        return None
    return minute if minute >= 0 else None


def post_goal_freshness(record: dict[str, Any]) -> tuple[bool, str]:
    """Require genuinely new LIVE evidence after the latest goal.

    A clock-only cooldown is insufficient: after a goal the previous pressure
    window may still be present in the record. Require a configurable number of
    post-goal match minutes plus at least one recent-window evidence item whose
    window starts after the goal.
    """
    match = record.get("match") or {}
    current = _minute(match.get("minute"))
    if current is None or current <= 0:
        return False, "post_goal_freshness_no_minute"

    timeline = record.get("goal_timeline") or record.get("timeline") or []
    goal_minutes: list[int] = []
    for item in timeline if isinstance(timeline, list) else []:
        if not isinstance(item, dict):
            continue
        minute = _minute(item.get("minute"))
        if minute is not None and minute <= current:
            goal_minutes.append(minute)

    explicit = _minute(record.get("last_goal_minute"))
    if explicit is None:
        explicit = _minute(match.get("last_goal_minute"))
    if explicit is not None and explicit <= current:
        goal_minutes.append(explicit)
    if not goal_minutes:
        return True, ""

    last_goal = max(goal_minutes)
    min_minutes = max(1, int(os.getenv("GOOL_POST_GOAL_FRESH_MINUTES", "5")))
    if current - last_goal < min_minutes:
        return False, f"post_goal_freshness_{min_minutes}m"

    # Recent pressure windows are produced by the live analyzer. At least the
    # 5-minute window must now lie completely after the goal; otherwise its
    # pressure still contains pre-goal evidence.
    live = record.get("live_analysis") or record.get("another_goal_live") or {}
    window = live.get("recent_5m") if isinstance(live, dict) else None
    if isinstance(window, dict):
        start = _minute(window.get("start_minute"))
        end = _minute(window.get("end_minute"))
        if start is not None and start < last_goal:
            return False, "post_goal_window_contains_pre_goal"
        if end is not None and end <= last_goal:
            return False, "post_goal_window_not_new"

    snapshots = record.get("snapshots") or record.get("live_snapshots") or []
    if isinstance(snapshots, list) and snapshots:
        post = 0
        for item in snapshots:
            if isinstance(item, dict):
                minute = _minute(item.get("minute"))
                if minute is not None and minute > last_goal:
                    post += 1
        required = max(2, int(os.getenv("GOOL_POST_GOAL_MIN_SNAPSHOTS", "2")))
        if post < required:
            return False, f"post_goal_snapshots_{post}_of_{required}"

    return True, ""
