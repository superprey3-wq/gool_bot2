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
    """Make ordinary GOOL a pure LIVE-Brain decision.

    The expert probability is the public/final football strength. 1xBet is only
    the source of the tradable market, freshness/score epoch and odd. Its steam,
    VALUE and opposition cannot raise, lower or revive an ordinary GOOL signal.
    Autonomous 1xBet STEAM is applied later by its separate layer.
    """
    try:
        strength = float(expert.get("probability"))
    except (TypeError, ValueError):
        strength = float(row.model_probability or 0.0)
    if strength > 1.0:
        strength /= 100.0
    strength = max(0.0, min(1.0, strength))
    row.model_probability = strength
    row.rating = round(strength * 100.0, 1)

    # Ordinary GOOL must never inherit router VALUE/market overrides. Those are
    # legacy routing aids; STEAM has its own autonomous product downstream.
    row.expected_roi = 0.0
    row.value_edge_pp = 0.0
    row.value_override = False
    row.market_override = False
    row.reason_tags = [
        tag
        for tag in row.reason_tags
        if tag
        not in {
            "strong_value",
            "value",
            "value_override",
            "market_override",
            "market_steam",
            "strong_market_opposition",
            "market_opposition",
        }
    ]
    if "live_brain_only" not in row.reason_tags:
        row.reason_tags.append("live_brain_only")
    if "goal_state_engine" not in row.reason_tags:
        row.reason_tags.append("goal_state_engine")

    # Remove only legacy soft router blocks. Hard time, stale/missing 1xBet price,
    # low data quality and other integrity guards remain intact.
    _remove_blocks(
        row,
        {
            "no_positive_value",
            "gool_wait_without_verified_override",
            "router_rating_below_62",
            "correlated_better_option",
            "goal_state_hard_no",
            "goal_state_borderline",
            "goal_state_borderline_without_market",
            "goal_state_no_data",
            "goal_state_no_data_without_market",
            "goal_state_rating_below_70",
            "goal_state_market_opposition",
        },
    )

    if row.odd < ABSOLUTE_MIN_BET_ODD and "price_too_low" not in row.blocks:
        row.blocks.append("price_too_low")

    state = str(expert.get("state") or ("PASS" if expert.get("passed") else "BORDERLINE")).upper()
    if state == "HARD_NO":
        row.blocks.append("goal_state_hard_no")
    elif state == "BORDERLINE":
        row.blocks.append("goal_state_borderline")
    elif state == "NO_DATA":
        row.blocks.append("goal_state_no_data")
    elif state != "PASS":
        row.blocks.append("goal_state_borderline")

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
        decision.reason = "WAIT: LIVE Brain не нашёл сценарий PASS с силой не ниже 70 при свежем кэфе 1xBet от 1.50."
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
                "GOOL LIVE Brain выбрал общий рынок на ещё один гол: он покрывает обе команды, а командный вариант не сильнее минимум на 6 пунктов.",
            )

    # From 55' do not chase two-goal confidence when a one-goal market is valid.
    if winner.strategy == "two_more_goals" and int(decision.minute) >= 55:
        if broad is not None and broad.key != winner.key:
            return _replace(
                decision,
                broad,
                "После 55-й минуты GOOL LIVE Brain предпочитает один гол сценарию +2.",
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
    """Final ordinary-GOOL policy before the independent STEAM layer.

    Every ordinary candidate is re-rated from its current LIVE expert
    probability, including the newer ``live_goal_hazard`` metric. Only PASS with
    LIVE-Brain strength >= GOOL_MULTI_MIN_RATING may bet. 1xBet movement cannot
    alter this decision; its autonomous STEAM layer runs later.
    """
    for row in _all_candidates(decision):
        expert = _expert(experts, row)
        if expert:
            _rate_core(row, expert)

    decision = _rerank(decision)
    decision = _coverage_rules(decision, experts)

    if decision.status == "BET" and decision.winner is not None:
        decision.reason = (
            f"GOOL LIVE Brain дал PASS {decision.winner.rating:.0f}/100; "
            "1xBet используется только как свежий рынок и кэф."
        )
    return decision
