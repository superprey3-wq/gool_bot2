from __future__ import annotations

import os
from typing import Any

from .multi_router import MarketCandidate, RouterDecision


_STRATEGY_EXPERT_KEY = {
    "another_goal": "another_goal",
    "two_more_goals": "two_more_goals",
    "goal_before_ht": "goal_before_ht",
    "home_goal": "home_goal",
    "away_goal": "away_goal",
    "both_teams_to_score": "btts",
}


def _metric(experts: dict[str, Any], strategy: str) -> str:
    key = _STRATEGY_EXPERT_KEY.get(str(strategy or ""))
    if key is None:
        return "probability"
    return str(((experts.get(key) or {}).get("metric") or "probability")).strip().lower()


def _all_candidates(decision: RouterDecision) -> list[MarketCandidate]:
    out: list[MarketCandidate] = []
    seen: set[int] = set()
    for row in [decision.winner, *decision.alternatives, *decision.rejected]:
        if row is None or id(row) in seen:
            continue
        seen.add(id(row))
        out.append(row)
    return out


def _confidence_rating(candidate: MarketCandidate) -> float:
    """Re-rate a heuristic confidence without pretending it is bet probability."""
    strength_score = max(0.0, min(100.0, float(candidate.model_probability) * 100.0))
    market_score = max(0.0, min(100.0, 50.0 + float(candidate.market_pressure_pp) * 5.0))
    data_score = max(0.0, min(100.0, float(candidate.data_quality) * 100.0))
    override_bonus = 6.0 if candidate.market_override else 0.0
    opposition_pp = max(0.0, -float(candidate.market_pressure_pp))
    opposition_penalty = min(12.0, max(0.0, opposition_pp - 3.0) * 2.0)
    return round(
        0.55 * strength_score
        + 0.20 * market_score
        + 0.25 * data_score
        + override_bonus
        - opposition_penalty,
        1,
    )


def _normalize_confidence_metrics(decision: RouterDecision, experts: dict[str, Any]) -> None:
    """Remove fake EV/value effects from heuristic confidence candidates.

    The legacy router receives confidence in the same numeric 0..1 slot as a
    calibrated probability. That must not turn e.g. GOOL confidence 0.73 at
    odds 3.14 into a literal +128% model ROI. We keep confidence as a strength
    signal, but value/EV overrides are disabled and the candidate is re-rated
    without any value/EV component.
    """
    for row in _all_candidates(decision):
        if _metric(experts, row.strategy) == "probability":
            continue

        row.expected_roi = 0.0
        row.value_edge_pp = 0.0
        row.value_override = False
        if not row.market_override:
            row.override_reason = None
        row.reason_tags = [
            tag for tag in row.reason_tags
            if tag not in {"strong_value", "value", "value_override"}
        ]
        row.rating = _confidence_rating(row)

        # These two blocks are probability/value-specific and must be rebuilt
        # after confidence has been separated from probability.
        row.blocks = [
            block for block in row.blocks
            if block not in {"no_positive_value", "gool_wait_without_verified_override"}
        ]
        if not row.expert_passed and not row.market_override:
            row.blocks.append("gool_wait_without_verified_override")
        if row.rating < 62.0 and "router_rating_below_62" not in row.blocks:
            row.blocks.append("router_rating_below_62")
        row.eligible = not row.blocks


def _rerank_after_metric_fix(decision: RouterDecision, experts: dict[str, Any]) -> RouterDecision:
    old_key = None if decision.winner is None else decision.winner.key
    _normalize_confidence_metrics(decision, experts)

    candidates = _all_candidates(decision)
    eligible = sorted(
        (row for row in candidates if row.eligible),
        key=lambda row: (float(row.rating), float(row.expected_roi), float(row.market_pressure_pp)),
        reverse=True,
    )
    rejected = [row for row in candidates if not row.eligible]

    shortlist: list[MarketCandidate] = []
    seen_correlations: set[str] = set()
    for row in eligible:
        correlation = row.correlation_key or row.key
        if correlation in seen_correlations:
            if "correlated_better_option" not in row.blocks:
                row.blocks.append("correlated_better_option")
            row.eligible = False
            rejected.append(row)
            continue
        seen_correlations.add(correlation)
        shortlist.append(row)

    if not shortlist:
        decision.status = "WAIT"
        decision.winner = None
        decision.alternatives = []
        decision.rejected = sorted(rejected, key=lambda row: row.rating, reverse=True)
        decision.reason = (
            "WAIT: после отделения GOOL confidence от калиброванной вероятности "
            "нет достаточно надёжного проходящего рынка."
        )
        return decision

    decision.status = "BET"
    decision.winner = shortlist[0]
    decision.alternatives = shortlist[1:4]
    decision.rejected = sorted(rejected, key=lambda row: row.rating, reverse=True)
    if decision.winner.key != old_key:
        decision.reason = (
            "Рынок пересчитан без ложного EV: GOOL confidence используется как сила LIVE-сценария, "
            "а не как буквальная вероятность ставки."
        )
    return decision


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
    decision.status = "BET"
    decision.winner = winner
    decision.alternatives = alternatives[:3]
    decision.reason = reason
    return decision


def _reject_team_winner(decision: RouterDecision, team: MarketCandidate) -> RouterDecision:
    if "heuristic_team_market_opposed" not in team.blocks:
        team.blocks.append("heuristic_team_market_opposed")
    if "heuristic_team_market_opposed" not in team.reason_tags:
        team.reason_tags.append("heuristic_team_market_opposed")
    team.eligible = False
    if not any(row.key == team.key for row in decision.rejected):
        decision.rejected.append(team)
    remaining = [row for row in decision.alternatives if row.eligible and row.key != team.key]
    if remaining:
        decision.winner = remaining[0]
        decision.alternatives = remaining[1:4]
        decision.reason = (
            "Узкий командный confidence-рынок отклонён: 1xBet движется против него; "
            "выбран следующий проходящий рынок Multi."
        )
        return decision
    decision.status = "WAIT"
    decision.winner = None
    decision.alternatives = []
    decision.reason = (
        "WAIT: единственный лучший командный рынок основан на GOOL confidence, а 1xBet движется против него. "
        "Без калиброванной вероятности такой узкий исход не отправляем."
    )
    return decision


def _safe_one_goal_after_late_minute(
    decision: RouterDecision,
    experts: dict[str, Any],
) -> RouterDecision:
    """Prefer one-goal coverage over heuristic +2 late in the match."""
    if decision.status != "BET" or decision.winner is None:
        return decision
    winner = decision.winner
    if winner.strategy != "two_more_goals":
        return decision

    try:
        safe_from = max(46, min(65, int(os.getenv("GOOL_MULTI_SAFE_ONE_GOAL_FROM_MINUTE", "55"))))
    except (TypeError, ValueError):
        safe_from = 55
    if int(decision.minute) < safe_from:
        return decision

    broad = _another_goal_option(decision)
    if broad is not None and broad.key != winner.key:
        winner.reason_tags.append("late_two_more_deprioritized")
        return _replace_winner(
            decision,
            broad,
            "После 55-й минуты приоритет у ЕЩЁ ОДНОГО ГОЛА: один гол надёжнее сценария +2. "
            "Агрессивный ТБ выше оставляем только альтернативой.",
        )

    # Current +2 expert is heuristic confidence, not a calibrated probability.
    # If the broad one-goal market itself does not pass, the safe action is WAIT,
    # not chasing a larger price that requires two goals.
    if _metric(experts, winner.strategy) != "probability":
        if "late_two_more_requires_calibrated_probability" not in winner.blocks:
            winner.blocks.append("late_two_more_requires_calibrated_probability")
        winner.reason_tags.append("late_two_more_deprioritized")
        winner.eligible = False
        if not any(row.key == winner.key for row in decision.rejected):
            decision.rejected.append(winner)

        safe = [
            row
            for row in decision.alternatives
            if row.eligible
            and row.goals_to_win == 1
            and _metric(experts, row.strategy) == "probability"
        ]
        if safe:
            return _replace_winner(
                decision,
                safe[0],
                "После 55-й минуты confidence-сценарий +2 отклонён; выбран проходящий "
                "калиброванный рынок на один гол.",
            )

        decision.status = "WAIT"
        decision.winner = None
        decision.alternatives = []
        decision.reason = (
            "WAIT: после 55-й минуты не берём +2 гола только по GOOL confidence. "
            "ТБ на ещё один гол тоже должен отдельно пройти модель и реальные кэфы 1xBet."
        )
    return decision


def enforce_goal_coverage(decision: RouterDecision, experts: dict[str, Any]) -> RouterDecision:
    """Apply confidence/value hygiene and prefer wider, safer goal coverage.

    Heuristic GOOL confidence is never allowed to masquerade as calibrated
    probability. Team +0.5 is narrower than `another_goal`, and late in the
    second half a heuristic two-more-goals path is also deliberately
    deprioritized behind a passing one-goal market.
    """
    decision = _rerank_after_metric_fix(decision, experts)
    if decision.status != "BET" or decision.winner is None:
        return decision

    team = decision.winner
    if team.family == "team_total":
        metric = _metric(experts, team.strategy)
        broad = _another_goal_option(decision)
        if broad is not None and broad.key != team.key:
            if metric != "probability":
                team.reason_tags.append("narrower_heuristic_market")
                decision = _replace_winner(
                    decision,
                    broad,
                    "Выбран ЕЩЁ ГОЛ: общий тотал выигрывает от гола любой команды; "
                    "командный рынок уже и основан на GOOL confidence, а не на калиброванной вероятности.",
                )
            else:
                min_rating_adv = float(os.getenv("GOOL_MULTI_TEAM_OVER_ANY_RATING_ADV", "6"))
                min_roi_adv = float(os.getenv("GOOL_MULTI_TEAM_OVER_ANY_ROI_ADV", "0.10"))
                rating_adv = float(team.rating) - float(broad.rating)
                roi_adv = float(team.expected_roi) - float(broad.expected_roi)
                if rating_adv < min_rating_adv or roi_adv < min_roi_adv:
                    team.reason_tags.append("narrower_market_insufficient_advantage")
                    decision = _replace_winner(
                        decision,
                        broad,
                        "Выбран ЕЩЁ ГОЛ: командный исход уже общего рынка и не даёт достаточного "
                        "преимущества по рейтингу и EV, чтобы оправдать потерю покрытия.",
                    )
        elif metric != "probability" and float(team.market_pressure_pp) < 0.0:
            decision = _reject_team_winner(decision, team)

    return _safe_one_goal_after_late_minute(decision, experts)
