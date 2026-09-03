from __future__ import annotations

import os
from typing import Any

from .multi_router import MarketCandidate, RouterDecision


def _team_expert_key(strategy: str) -> str | None:
    if strategy == "home_goal":
        return "home_goal"
    if strategy == "away_goal":
        return "away_goal"
    return None


def _metric(experts: dict[str, Any], strategy: str) -> str:
    key = _team_expert_key(strategy)
    if key is None:
        return "probability"
    return str(((experts.get(key) or {}).get("metric") or "probability")).strip().lower()


def _another_goal_option(decision: RouterDecision) -> MarketCandidate | None:
    rows = [decision.winner, *decision.alternatives]
    for row in rows:
        if row is not None and row.eligible and row.strategy == "another_goal":
            return row
    return None


def _replace_winner(decision: RouterDecision, winner: MarketCandidate, reason: str) -> RouterDecision:
    old = decision.winner
    alternatives: list[MarketCandidate] = []
    if old is not None and old.key != winner.key:
        alternatives.append(old)
    for row in decision.alternatives:
        if row.key == winner.key or any(existing.key == row.key for existing in alternatives):
            continue
        alternatives.append(row)
    decision.winner = winner
    decision.alternatives = alternatives[:3]
    decision.reason = reason
    return decision


def enforce_goal_coverage(decision: RouterDecision, experts: dict[str, Any]) -> RouterDecision:
    """Do not let a narrower heuristic team-goal market beat calibrated any-goal.

    Team +0.5 is a strict subset of the match's next-goal total: a goal by the
    opposite side wins `another_goal` but loses the team total.  A heuristic
    GOOL confidence therefore must not be treated as a calibrated probability
    and win purely because the bookmaker offers a larger price.

    A team market with an actual calibrated probability may still beat the
    wider market, but only with a material rating and EV advantage.
    """
    if decision.status != "BET" or decision.winner is None:
        return decision
    team = decision.winner
    if team.family != "team_total":
        return decision
    broad = _another_goal_option(decision)
    if broad is None or broad.key == team.key:
        return decision

    metric = _metric(experts, team.strategy)
    if metric != "probability":
        team.reason_tags.append("narrower_heuristic_market")
        return _replace_winner(
            decision,
            broad,
            "Выбран ЕЩЁ ГОЛ: общий тотал выигрывает от гола любой команды; "
            "командный рынок уже и основан на GOOL confidence, а не на калиброванной вероятности.",
        )

    min_rating_adv = float(os.getenv("GOOL_MULTI_TEAM_OVER_ANY_RATING_ADV", "6"))
    min_roi_adv = float(os.getenv("GOOL_MULTI_TEAM_OVER_ANY_ROI_ADV", "0.10"))
    rating_adv = float(team.rating) - float(broad.rating)
    roi_adv = float(team.expected_roi) - float(broad.expected_roi)
    if rating_adv < min_rating_adv or roi_adv < min_roi_adv:
        team.reason_tags.append("narrower_market_insufficient_advantage")
        return _replace_winner(
            decision,
            broad,
            "Выбран ЕЩЁ ГОЛ: командный исход уже общего рынка и не даёт достаточного "
            "преимущества по рейтингу и EV, чтобы оправдать потерю покрытия.",
        )
    return decision
