from __future__ import annotations

from typing import Any

from .value_bet_policy import ABSOLUTE_MIN_BET_ODD

FIRST_HALF_STRATEGY = "goal_before_ht"
SECOND_HALF_STRATEGY = "another_goal"
FIRST_HALF_MAX_MINUTE = 45
SECOND_HALF_MIN_MINUTE = 46
SECOND_HALF_MAX_MINUTE = 75
MIN_BET_ODD = ABSOLUTE_MIN_BET_ODD


def ordinary_strategy(match: dict[str, Any]) -> str | None:
    """Return the only ordinary GOOL strategy allowed in the current phase.

    New production concept:
    - first half: goal before half-time only;
    - second half: one more goal only, through 75';
    - autonomous 1xBet STEAM is applied later and is intentionally not filtered here.
    """
    if bool(match.get("is_finished")) or bool(match.get("is_halftime")):
        return None

    try:
        minute = int(match.get("minute") or 0)
    except (TypeError, ValueError):
        minute = 0

    if 0 < minute <= FIRST_HALF_MAX_MINUTE:
        return FIRST_HALF_STRATEGY
    if SECOND_HALF_MIN_MINUTE <= minute <= SECOND_HALF_MAX_MINUTE:
        return SECOND_HALF_STRATEGY
    return None


def routing_experts(match: dict[str, Any], experts: dict[str, Any]) -> dict[str, Any]:
    """Expose only the active ordinary system to the market router.

    The full expert set is kept outside this function for diagnostics, journal
    context and autonomous STEAM confidence. Only ordinary production routing is
    reduced to the two-system concept.
    """
    strategy = ordinary_strategy(match)
    if strategy is None or strategy not in experts:
        return {}
    return {strategy: experts[strategy]}
