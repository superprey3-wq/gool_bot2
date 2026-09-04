from __future__ import annotations

import os
from typing import Any

from .multi_router import MarketCandidate, RouterDecision


_EXPERT_BY_STRATEGY = {
    "another_goal": "another_goal",
    "two_more_goals": "two_more_goals",
    "goal_before_ht": "goal_before_ht",
    "home_goal": "home_goal",
    "away_goal": "away_goal",
    "both_teams_to_score": "btts",
}

# These are confidence floors, not calibrated probabilities.
# Strong markets from the first production evening stay at the existing 70 floor.
_DEFAULT_RATING_FLOORS = {
    "another_goal": 77.0,
    "goal_before_ht": 78.0,
    "two_more_goals": 77.0,
    "home_goal": 73.0,
    "away_goal": 70.0,
    "both_teams_to_score": 70.0,
}

_DEFAULT_FOOTBALL_FLOORS = {
    "another_goal": 0.75,
    "goal_before_ht": 0.76,
    "two_more_goals": 0.74,
    "home_goal": 0.70,
    "away_goal": 0.0,
    "both_teams_to_score": 0.0,
}

_ENV_PREFIX = {
    "another_goal": "ANOTHER_GOAL",
    "goal_before_ht": "FIRST_HALF",
    "two_more_goals": "TWO_MORE",
    "home_goal": "HOME_GOAL",
    "away_goal": "AWAY_GOAL",
    "both_teams_to_score": "BTTS",
}

_STRONG_STEAM_LEVELS = {"STRONG_STEAM", "MULTI_MARKET_STEAM"}


def _f(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        return default


def _football_strength(experts: dict[str, Any], row: MarketCandidate) -> float:
    key = _EXPERT_BY_STRATEGY.get(str(row.strategy or ""))
    expert = dict(experts.get(key) or {}) if key else {}
    try:
        value = float(expert.get("probability"))
    except (TypeError, ValueError):
        value = float(row.model_probability or 0.0)
    if value > 1.0:
        value /= 100.0
    return max(0.0, min(1.0, value))


def _strong_steam_support(row: MarketCandidate) -> bool:
    return bool(
        row.market_override
        and str(row.market_level or "").upper() in _STRONG_STEAM_LEVELS
        and float(row.market_pressure_pp or 0.0) > 0.0
    )


def _floors(row: MarketCandidate) -> tuple[float, float, bool]:
    strategy = str(row.strategy or "")
    prefix = _ENV_PREFIX.get(strategy)
    rating_default = _DEFAULT_RATING_FLOORS.get(strategy, 70.0)
    football_default = _DEFAULT_FOOTBALL_FLOORS.get(strategy, 0.0)

    rating_floor = rating_default
    football_floor = football_default
    if prefix:
        rating_floor = _f(f"GOOL_CONFIDENCE_{prefix}_MIN_RATING", rating_default)
        football_floor = _f(f"GOOL_CONFIDENCE_{prefix}_MIN_FOOTBALL", football_default)

    steam = _strong_steam_support(row)
    if steam:
        # STEAM helps a football idea; it never replaces it. Even after the
        # relief the weak products remain stricter than the old global 70 gate.
        rating_floor -= max(0.0, _f("GOOL_CONFIDENCE_STEAM_RATING_RELIEF", 2.0))
        football_floor -= max(0.0, _f("GOOL_CONFIDENCE_STEAM_FOOTBALL_RELIEF", 0.02))

    return max(70.0, rating_floor), max(0.0, football_floor), steam


def _eligible_after_confidence(
    row: MarketCandidate,
    experts: dict[str, Any],
) -> tuple[bool, float, float, float, bool]:
    rating_floor, football_floor, steam = _floors(row)
    football = _football_strength(experts, row)
    rating = float(row.rating or 0.0)
    ok = rating >= rating_floor and football >= football_floor
    return ok, rating_floor, football_floor, football, steam


def _mark_rejected(
    row: MarketCandidate,
    *,
    rating_floor: float,
    football_floor: float,
    football: float,
) -> None:
    if float(row.rating or 0.0) < rating_floor:
        row.blocks.append(f"confidence_rating_below:{float(row.rating or 0.0):.1f}<{rating_floor:.1f}")
    if football < football_floor:
        row.blocks.append(f"confidence_football_below:{football * 100.0:.1f}<{football_floor * 100.0:.1f}")
    row.blocks = list(dict.fromkeys(row.blocks))
    if "confidence_gate" not in row.reason_tags:
        row.reason_tags.append("confidence_gate")
    row.eligible = False


def enforce_confidence_gate(
    decision: RouterDecision,
    experts: dict[str, Any],
) -> RouterDecision:
    """Demand extra football certainty for historically weaker GOOL products.

    This gate runs after the Goal State policy and before autonomous STEAM.
    It never changes the bookmaker minimum odd. Strong verified 1xBet steam can
    provide a small confirmation relief, but cannot resurrect a weak scenario.
    """
    if decision.status != "BET" or decision.winner is None:
        return decision

    rows: list[MarketCandidate] = []
    for row in [decision.winner, *decision.alternatives]:
        if row is None or not row.eligible or any(existing.key == row.key for existing in rows):
            continue
        rows.append(row)

    passed: list[MarketCandidate] = []
    failed: list[MarketCandidate] = []
    steam_supported: set[str] = set()

    for row in rows:
        ok, rating_floor, football_floor, football, steam = _eligible_after_confidence(row, experts)
        if ok:
            passed.append(row)
            if steam:
                steam_supported.add(row.key)
            continue
        _mark_rejected(
            row,
            rating_floor=rating_floor,
            football_floor=football_floor,
            football=football,
        )
        failed.append(row)

    for row in failed:
        if not any(item.key == row.key for item in decision.rejected):
            decision.rejected.append(row)

    if not passed:
        decision.status = "WAIT"
        decision.winner = None
        decision.alternatives = []
        decision.reason = (
            "WAIT: футбольная уверенность ниже усиленного порога. "
            "Коэффициент сам по себе не является причиной для входа."
        )
        return decision

    passed.sort(
        key=lambda row: (
            float(row.rating or 0.0),
            float(row.market_pressure_pp or 0.0),
            -int(row.goals_to_win or 1),
            float(row.odd or 0.0),
        ),
        reverse=True,
    )
    previous_key = decision.winner.key
    decision.status = "BET"
    decision.winner = passed[0]
    decision.alternatives = passed[1:4]

    if decision.winner.key != previous_key:
        decision.reason = (
            "GOOL отбросил менее уверенный сценарий и выбрал следующий "
            "проходящий вариант с более сильным футбольным подтверждением."
        )
    elif decision.winner.key in steam_supported:
        decision.reason = (
            "GOOL Goal State прошёл повышенный порог уверенности; "
            "сильный свежий STEAM 1xBet дополнительно подтвердил вход."
        )
    else:
        decision.reason = (
            "GOOL Goal State прошёл повышенный порог футбольной уверенности; "
            "1xBet используется как реальная цена и контроль рынка."
        )
    return decision
