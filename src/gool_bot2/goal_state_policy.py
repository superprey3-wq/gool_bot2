from __future__ import annotations

import os
from typing import Any

from .multi_router import MarketCandidate, RouterDecision
from .value_bet_policy import ABSOLUTE_MIN_BET_ODD


_STRATEGY_EXPERT = {
    "another_goal": "another_goal",
    "two_more_goals": "two_more_goals",
    "goal_before_ht": "goal_before_ht",
    "home_goal": "home_goal",
    "away_goal": "away_goal",
    "both_teams_to_score": "btts",
}


def _f(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        return default


def _expert(experts: dict[str, Any], candidate: MarketCandidate) -> dict[str, Any]:
    key = _STRATEGY_EXPERT.get(str(candidate.strategy or ""))
    return dict(experts.get(key) or {}) if key else {}


def _all_candidates(decision: RouterDecision) -> list[MarketCandidate]:
    rows: list[MarketCandidate] = []
    seen: set[int] = set()
    for row in [decision.winner, *decision.alternatives, *decision.rejected]:
        if row is None or id(row) in seen:
            continue
        seen.add(id(row))
        rows.append(row)
    return rows


def _remove_blocks(row: MarketCandidate, names: set[str]) -> None:
    row.blocks = [block for block in row.blocks if block not in names]


def _rate_core(row: MarketCandidate, expert: dict[str, Any]) -> None:
    """Re-rate ordinary GOOL from football state, not from price/value.

    1xBet is still mandatory as the real tradable market and contributes a
    small confirmation/opposition component. VALUE never turns a weak football
    state into a strong one.
    """
    try:
        strength = float(expert.get("probability"))
    except (TypeError, ValueError):
        strength = float(row.model_probability or 0.0)
    strength = max(0.0, min(1.0, strength))

    market_score = max(0.0, min(100.0, 50.0 + float(row.market_pressure_pp or 0.0) * 5.0))
    data_score = max(0.0, min(100.0, float(row.data_quality or 0.0) * 100.0))
    rating = 0.82 * (strength * 100.0) + 0.12 * data_score + 0.06 * market_score

    state = str(expert.get("state") or ("PASS" if expert.get("passed") else "BORDERLINE")).upper()
    if state in {"BORDERLINE", "NO_DATA"} and row.market_override:
        rating += 3.0

    opposition = max(0.0, -float(row.market_pressure_pp or 0.0))
    rating -= min(10.0, max(0.0, opposition - 3.0) * 1.5)
    row.rating = round(max(0.0, min(99.0, rating)), 1)

    # The state engine is confidence, not a calibrated betting probability.
    row.expected_roi = 0.0
    row.value_edge_pp = 0.0
    row.value_override = False
    row.reason_tags = [
        tag for tag in row.reason_tags
        if tag not in {"strong_value", "value", "value_override"}
    ]
    if "confidence_metric" not in row.reason_tags:
        row.reason_tags.append("confidence_metric")
    if "goal_state_engine" not in row.reason_tags:
        row.reason_tags.append("goal_state_engine")

    _remove_blocks(
        row,
        {
            "no_positive_value",
            "gool_wait_without_verified_override",
            "router_rating_below_62",
            "correlated_better_option",
        },
    )

    state_blocks = {
        "goal_state_hard_no",
        "goal_state_borderline_without_market",
        "goal_state_no_data_without_market",
        "goal_state_rating_below_70",
        "goal_state_market_opposition",
    }
    _remove_blocks(row, state_blocks)

    if row.odd < ABSOLUTE_MIN_BET_ODD and "price_too_low" not in row.blocks:
        row.blocks.append("price_too_low")

    if state == "HARD_NO":
        row.blocks.append("goal_state_hard_no")
    elif state == "BORDERLINE" and not row.market_override:
        row.blocks.append("goal_state_borderline_without_market")
    elif state == "NO_DATA" and not row.market_override:
        row.blocks.append("goal_state_no_data_without_market")

    if float(row.market_pressure_pp or 0.0) <= _f("GOOL_STATE_HARD_MARKET_OPPOSITION_PP", -6.0):
        row.blocks.append("goal_state_market_opposition")

    min_rating = _f("GOOL_MULTI_MIN_RATING", 70.0)
    if row.rating < min_rating:
        row.blocks.append("goal_state_rating_below_70")

    row.blocks = list(dict.fromkeys(row.blocks))
    row.eligible = not row.blocks


def _rerank(decision: RouterDecision) -> RouterDecision:
    rows = _all_candidates(decision)
    eligible = sorted(
        (row for row in rows if row.eligible),
        key=lambda row: (
            float(row.rating),
            float(row.market_pressure_pp),
            -int(row.goals_to_win),
            float(row.odd),
        ),
        reverse=True,
    )
    rejected = [row for row in rows if not row.eligible]

    shortlist: list[MarketCandidate] = []
    seen: set[str] = set()
    for row in eligible:
        correlation = row.correlation_key or row.key
        if correlation in seen:
            row.blocks.append("correlated_better_option")
            row.eligible = False
            rejected.append(row)
            continue
        seen.add(correlation)
        shortlist.append(row)

    decision.rejected = sorted(rejected, key=lambda row: float(row.rating), reverse=True)
    if not shortlist:
        decision.status = "WAIT"
        decision.winner = None
        decision.alternatives = []
        decision.reason = "WAIT: единый GOOL Goal State не нашёл достаточно сильного футбольного сценария при кэфе 1xBet от 1.40."
        return decision

    decision.status = "BET"
    decision.winner = shortlist[0]
    decision.alternatives = shortlist[1:4]
    return decision


def _find(decision: RouterDecision, strategy: str) -> MarketCandidate | None:
    for row in [decision.winner, *decision.alternatives]:
        if row is not None and row.eligible and row.strategy == strategy:
            return row
    return None


def _replace(decision: RouterDecision, winner: MarketCandidate, reason: str) -> RouterDecision:
    old = decision.winner
    alternatives: list[MarketCandidate] = []
    if old is not None and old.key != winner.key and old.eligible:
        alternatives.append(old)
    for row in decision.alternatives:
        if row.key == winner.key or not row.eligible or any(x.key == row.key for x in alternatives):
            continue
        alternatives.append(row)
    decision.winner = winner
    decision.alternatives = alternatives[:3]
    decision.status = "BET"
    decision.reason = reason
    return decision


def _coverage_rules(decision: RouterDecision, experts: dict[str, Any]) -> RouterDecision:
    if decision.status != "BET" or decision.winner is None:
        return decision

    winner = decision.winner
    broad = _find(decision, "another_goal")

    # A team goal is narrower than "one more goal". Prefer wider coverage unless
    # the team-specific state is materially stronger.
    if winner.family == "team_total" and broad is not None and broad.key != winner.key:
        advantage = float(winner.rating) - float(broad.rating)
        required = _f("GOOL_STATE_TEAM_OVER_ANY_ADVANTAGE", 6.0)
        if advantage < required:
            return _replace(
                decision,
                broad,
                "GOOL Goal State выбрал общий рынок на ещё один гол: он покрывает обе команды, а командный вариант не сильнее минимум на 6 пунктов.",
            )

    # From 55' do not chase two-goal confidence when a one-goal market is valid.
    if winner.strategy == "two_more_goals" and int(decision.minute) >= 55:
        if broad is not None and broad.key != winner.key:
            return _replace(
                decision,
                broad,
                "После 55-й минуты GOOL Goal State предпочитает один гол сценарию +2.",
            )
        winner.blocks.append("late_two_more_without_one_goal_cover")
        winner.eligible = False
        if not any(row.key == winner.key for row in decision.rejected):
            decision.rejected.append(winner)
        decision.status = "WAIT"
        decision.winner = None
        decision.alternatives = []
        decision.reason = "WAIT: после 55-й минуты не берём +2 без проходящего сценария на один гол."
        return decision

    # BTTS from 0:0 also needs two future goals; treat it conservatively late.
    if (
        winner.strategy == "both_teams_to_score"
        and tuple(decision.score) == (0, 0)
        and int(decision.minute) >= 55
    ):
        if broad is not None and broad.key != winner.key:
            return _replace(
                decision,
                broad,
                "При 0:0 после 55-й ОЗ требует два будущих гола; безопаснее проходящий рынок на один гол.",
            )
        winner.blocks.append("late_btts_zero_zero_requires_two_goals")
        winner.eligible = False
        if not any(row.key == winner.key for row in decision.rejected):
            decision.rejected.append(winner)
        decision.status = "WAIT"
        decision.winner = None
        decision.alternatives = []
        decision.reason = "WAIT: ОЗ при 0:0 после 55-й требует два гола и не имеет более широкого проходящего покрытия."
        return decision

    return decision


def enforce_goal_state_policy(
    decision: RouterDecision,
    experts: dict[str, Any],
) -> RouterDecision:
    """Final ordinary-GOOL policy before the independent steam layer.

    PASS can bet. BORDERLINE/NO_DATA need verified 1xBet market override.
    HARD_NO can never be revived by VALUE or ordinary market logic.
    Exceptional autonomous steam is applied later by a separate module.
    """
    for row in _all_candidates(decision):
        expert = _expert(experts, row)
        if expert and str(expert.get("metric") or "").lower() == "confidence":
            _rate_core(row, expert)

    decision = _rerank(decision)
    decision = _coverage_rules(decision, experts)

    if decision.status == "BET" and decision.winner is not None:
        expert = _expert(experts, decision.winner)
        state = str(expert.get("state") or "PASS").upper()
        if not decision.reason or "VALUE" in decision.reason or "баланс" in decision.reason:
            if state in {"BORDERLINE", "NO_DATA"} and decision.winner.market_override:
                decision.reason = "GOOL Goal State был пограничным, но сильное подтверждённое движение 1xBet разрешило вход."
            else:
                decision.reason = "GOOL Goal State дал PASS; 1xBet используется как реальная цена, фильтр кэфа и подтверждение рынка."
    return decision
