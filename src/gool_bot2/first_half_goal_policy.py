from __future__ import annotations

import os
from typing import Any


def _float_env(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        return default


def _int_env(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        return default


def primary_market_odd(info: dict[str, Any] | None) -> float | None:
    data = info or {}
    targets = [row for row in (data.get("targets") or []) if isinstance(row, dict)]
    if not targets:
        return None
    target = max(targets, key=lambda row: float(row.get("weight") or 0.0))
    selection = target.get("selection") if isinstance(target.get("selection"), dict) else {}
    try:
        odd = float(selection.get("odd"))
    except (TypeError, ValueError):
        return None
    return odd if odd > 1.0 else None


def first_half_goal_decision(
    *,
    minute: int,
    is_halftime: bool,
    is_finished: bool,
    probability: Any,
    analyzer_passed: bool,
    market_info: dict[str, Any] | None,
    last_goal_minute: int | None,
    already_recorded: bool,
    open_signals: int,
) -> dict[str, Any]:
    """Conservative gate for one more goal before half-time.

    The exact first-half bookmaker market is not assumed here. 1xBet's dynamic
    next-goal/full-match total is used only as a fresh market-pressure confirmation,
    while the shorter horizon is enforced by minute, model probability and GOOL LIVE.
    """

    min_minute = _int_env("FIRST_HALF_GOAL_MIN_MINUTE", 15)
    max_minute = _int_env("FIRST_HALF_GOAL_MAX_MINUTE", 40)
    min_probability = _float_env("FIRST_HALF_GOAL_MIN_PROBABILITY", 0.68)
    min_odd = _float_env("FIRST_HALF_GOAL_MIN_ODD", 1.40)
    max_odd = _float_env("FIRST_HALF_GOAL_MAX_ODD", 4.00)
    cooldown = _int_env("FIRST_HALF_GOAL_POST_GOAL_COOLDOWN_MINUTES", 5)
    max_open = _int_env("FIRST_HALF_GOAL_MAX_OPEN_SIGNALS", 2)

    reasons: list[str] = []
    try:
        p = float(probability)
    except (TypeError, ValueError):
        p = -1.0
    if p > 1.0:
        p /= 100.0

    if is_finished:
        reasons.append("match_finished")
    if is_halftime or minute >= 46:
        reasons.append("first_half_closed")
    if minute < min_minute:
        reasons.append(f"first_half_warmup_until_{min_minute}")
    if minute > max_minute:
        reasons.append(f"first_half_entry_window_closed_{max_minute}")
    if p < min_probability:
        reasons.append(f"model_probability={max(0.0, p):.3f}<{min_probability:.3f}")
    if not analyzer_passed:
        reasons.append("gool_live_not_confirmed")
    if already_recorded:
        reasons.append("first_half_goal_already_recorded")
    if open_signals >= max_open:
        reasons.append(f"open_exposure={open_signals}>={max_open}")
    if last_goal_minute is not None and minute - int(last_goal_minute) < cooldown:
        reasons.append(f"post_goal_cooldown_{cooldown}")

    info = dict(market_info or {})
    level = str(info.get("level") or "NO_DATA")
    reason_text = str(info.get("reason") or "").upper()
    hard_bad = any(token in level.upper() or token in reason_text for token in ("DESYNC", "REPRICE", "UNAVAILABLE"))
    if hard_bad:
        reasons.append(f"market_event_guard:{level}")
    if not bool(info.get("override_fresh", False)):
        reasons.append("market_not_fresh")
    if not bool(info.get("confirmed", False)):
        reasons.append(f"market_not_confirmed:{level}")

    odd = primary_market_odd(info)
    if odd is None:
        reasons.append("market_odd_unavailable")
    elif odd < min_odd:
        reasons.append(f"low_odd:{odd:.3f}<{min_odd:.2f}")
    elif odd > max_odd:
        reasons.append(f"high_odd:{odd:.3f}>{max_odd:.2f}")

    return {
        "passed": not reasons,
        "reasons": reasons,
        "probability": None if p < 0 else p,
        "market_odd": odd,
        "min_minute": min_minute,
        "max_minute": max_minute,
        "min_probability": min_probability,
        "min_odd": min_odd,
        "max_odd": max_odd,
        "cooldown_minutes": cooldown,
    }
