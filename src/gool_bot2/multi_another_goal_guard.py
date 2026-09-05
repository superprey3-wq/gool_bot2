from __future__ import annotations

import os
from typing import Any

from .xbet_market_pressure import live_1x2_context


def _f(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        return default


def _last_goal_minute(record: dict[str, Any]) -> int | None:
    timeline = (((record.get("providers") or {}).get("flashscore") or {}).get("meta") or {}).get("goal_timeline") or []
    latest: int | None = None
    for row in timeline:
        if not isinstance(row, dict):
            continue
        event_type = str(row.get("event_type") or "goal").lower()
        if event_type != "goal":
            continue
        try:
            minute = int(float(row.get("minute") or 0))
        except (TypeError, ValueError):
            continue
        if minute <= 0:
            continue
        latest = minute if latest is None else max(latest, minute)
    return latest


def _football_probability(experts: dict[str, Any]) -> float:
    try:
        value = float((experts.get("another_goal") or {}).get("probability") or 0.0)
    except (TypeError, ValueError):
        value = 0.0
    if value > 1.0:
        value /= 100.0
    return max(0.0, min(1.0, value))


def _line_probability(record: dict[str, Any]) -> float | None:
    profile = record.get("prematch_goal_profile") or {}
    active = profile.get("active") or {}
    if str(active.get("period") or "") != "2H":
        return None
    try:
        half_goals = int(active.get("current_half_goals") or 0)
    except (TypeError, ValueError):
        return None
    line = float(half_goals) + 0.5
    second_half = profile.get("second_half") or {}
    value = (second_half.get("over") or {}).get(f"{line:.1f}")
    try:
        return None if value is None else max(0.0, min(1.0, float(value)))
    except (TypeError, ValueError):
        return None


def _block(decision: Any, reason_tag: str, reason: str) -> Any:
    winner = getattr(decision, "winner", None)
    if winner is None:
        return decision
    if reason_tag not in winner.blocks:
        winner.blocks.append(reason_tag)
    if reason_tag not in winner.reason_tags:
        winner.reason_tags.append(reason_tag)
    winner.eligible = False
    if not any(row.key == winner.key for row in decision.rejected):
        decision.rejected.append(winner)
    decision.status = "WAIT"
    decision.winner = None
    decision.alternatives = []
    decision.reason = reason
    return decision


def enforce_another_goal_context(
    decision: Any,
    record: dict[str, Any],
    experts: dict[str, Any],
    market_row: dict[str, Any] | None,
) -> Any:
    """Protect ordinary `another_goal` from chasing a just-realized goal state.

    1X2 is used as context, never as a standalone goal trigger. A strong move
    toward the draw on a tied high-scoring state is market opposition and asks
    for unusually strong football + total-market confirmation. Historical 2H
    line probability is also respected when the half has already delivered
    multiple goals.
    """
    if str(getattr(decision, "status", "")) != "BET" or getattr(decision, "winner", None) is None:
        return decision
    winner = decision.winner
    if str(getattr(winner, "strategy", "")) != "another_goal":
        return decision
    if str(getattr(winner, "source", "")).startswith("1xbet:autonomous_steam"):
        return decision

    match = record.get("match") or {}
    minute = int(match.get("minute") or 0)
    hs = int(match.get("home_score") or 0)
    aws = int(match.get("away_score") or 0)

    one_x_two = live_1x2_context(market_row)
    record["xbet_live_1x2"] = one_x_two

    last_goal = _last_goal_minute(record)
    wait_minutes = max(0, int(_f("GOOL_ANOTHER_GOAL_POST_GOAL_WAIT_MINUTES", 3.0)))
    if last_goal is not None and wait_minutes > 0 and 0 <= minute - last_goal < wait_minutes:
        return _block(
            decision,
            "another_goal_post_goal_rebuild",
            f"WAIT: после гола на {last_goal}' ждём {wait_minutes} мин., чтобы LIVE давление сформировалось заново.",
        )

    football = _football_probability(experts)
    total_pressure = float(getattr(winner, "market_pressure_pp", 0.0) or 0.0)
    strong_football = football >= _f("GOOL_ANOTHER_GOAL_STRICT_FOOTBALL", 0.82)
    total_confirmed = total_pressure >= _f("GOOL_ANOTHER_GOAL_STRICT_MARKET_PP", 3.0)

    if hs == aws and hs + aws >= 2 and one_x_two.get("available"):
        fair = one_x_two.get("fair") or {}
        deltas = one_x_two.get("delta_pp") or {}
        draw_fair = float(fair.get("draw") or 0.0)
        draw_delta = float(deltas.get("draw") or 0.0)
        if (
            draw_fair >= _f("GOOL_ANOTHER_GOAL_DRAW_FAIR_OPPOSITION", 0.45)
            and draw_delta >= _f("GOOL_ANOTHER_GOAL_DRAW_MOVE_OPPOSITION_PP", 3.0)
            and not (strong_football and total_confirmed)
        ):
            return _block(
                decision,
                "another_goal_1x2_draw_repricing",
                "WAIT: при равном счёте 1xBet усиливает вероятность ничьей; для ещё одного гола нет двойного подтверждения LIVE + тотал.",
            )

    active = (record.get("prematch_goal_profile") or {}).get("active") or {}
    half_goals = int(active.get("current_half_goals") or 0) if str(active.get("period") or "") == "2H" else 0
    line_prob = _line_probability(record)
    if (
        half_goals >= 2
        and line_prob is not None
        and line_prob < _f("GOOL_ANOTHER_GOAL_LOW_LINE_PROB", 0.35)
        and not (strong_football and total_confirmed)
    ):
        return _block(
            decision,
            "another_goal_half_total_saturated",
            "WAIT: второй тайм уже реализовал высокий голевой объём относительно prematch-профиля; нужен усиленный LIVE + рынок.",
        )

    return decision


__all__ = ["enforce_another_goal_context"]
