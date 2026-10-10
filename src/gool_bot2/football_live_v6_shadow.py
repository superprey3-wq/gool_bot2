"""Football LIVE V6 audit-only challenger on a single immutable match snapshot.

Compare actual production Brain V3 with existing V4 goal-probability and
hazard models. All three use overlapping match statistics, so agreement
does NOT create independent confirmation or justify publication. Horizons
differ (45/90 for V4; configurable 46/93 for hazard) and are explicitly logged.

Do not modify, gate, publish, settle, or assign betting odds.
"""
from __future__ import annotations

import math
from typing import Any

from .live_goal_brain_v4 import evaluate_live_goals
from .live_goal_hazard import estimate_live_goal_hazard


def football_live_v6_shadow(
    record: dict[str, Any],
    production_decision: dict[str, Any],
) -> dict[str, Any]:
    match = dict(record.get("match") or {})
    name = str(production_decision.get("strategy") or "")
    base: dict[str, Any] = {
        "version": "football_live_v6_shadow",
        "status": "WAIT",
        "strategy": name,
        "event_id": str(match.get("flashscore_event_id") or ""),
        "brain_v3_status": str(production_decision.get("status") or ""),
        "brain_v3_probability_uncalibrated": production_decision.get("probability"),
        "advisory_only": True,
        "correlated_sources_not_independent_votes": True,
        "probabilities_calibrated": False,
    }
    if name not in {"goal_before_ht", "another_goal"}:
        return {**base, "reason": "unsupported_goal_market"}
    if match.get("is_finished") or match.get("is_halftime"):
        return {**base, "reason": "not_active_play"}
    try:
        minute = int(match.get("minute"))
    except (TypeError, ValueError):
        return {**base, "reason": "minute_unavailable"}
    if minute < 1 or minute > 95:
        return {**base, "reason": "not_active_play"}
    if name == "goal_before_ht" and minute > 45:
        return {**base, "reason": "first_half_complete"}
    if name == "another_goal" and minute < 46:
        return {**base, "reason": "second_half_not_started"}

    # These are existing production calculation modules, merely observed here.
    # Exception handling is fail-closed and must NOT alter the bet decision.
    try:
        hazard = estimate_live_goal_hazard(
            record, period="1H" if name == "goal_before_ht" else "FT",
        )
    except (TypeError, KeyError, IndexError, ValueError, ZeroDivisionError) as exc:
        hazard = {"available": False, "passed": False, "blocks": ["hazard_error_" + type(exc).__name__]}
    try:
        mapping = {"goal_before_ht": "GOAL_BEFORE_HT", "another_goal": "ANOTHER_GOAL"}
        v4 = next(
            (d for d in evaluate_live_goals(record) if d.market == mapping[name]), None
        )
    except (TypeError, KeyError, IndexError, ValueError, ZeroDivisionError) as exc:
        v4 = None
        v4_error = type(exc).__name__
    else:
        v4_error = None

    v3p = production_decision.get("probability")
    vp = None if v4 is None else v4.probability
    hp = hazard.get("probability")
    numeric: list[float] = []
    for p in (v3p, vp, hp):
        try:
            value = float(p)
        except (TypeError, ValueError):
            continue
        if math.isfinite(value) and 0 <= value <= 1:
            numeric.append(value)
    availability = {
        "brain_v3": bool(numeric) and v3p is not None,
        "live_v4": v4 is not None,
        "hazard": bool(hazard.get("available")) and hp is not None,
    }
    v4_dict = None if v4 is None else v4.to_dict()
    drift = round((max(numeric) - min(numeric)) * 100, 2) if len(numeric) >= 2 else None
    has_comparison = bool(v4 is not None and hazard.get("available"))
    actual_v3 = str(production_decision.get("status") or "").upper()
    v4_lean = (v4.decision == "BET") if v4 else None
    hazard_lean = bool(hazard.get("passed")) if hazard.get("available") else None
    return {
        **base,
        "status": "COMPARED" if has_comparison else "WAIT",
        "reason": "all_models_observed_correlated" if has_comparison else "incomplete_live_evidence",
        "minute": minute,
        "match_score": [
            int(match.get("home_score") or 0), int(match.get("away_score") or 0),
        ],
        "available": availability,
        "live_v4": v4_dict,
        "live_v4_error": v4_error,
        "hazard": hazard,
        "probability_spread_pp_not_same_horizon": drift,
        "brain_v3_bet": actual_v3 == "BET",
        "live_v4_bet": v4_lean,
        "hazard_passed": hazard_lean,
        "agreement_diagnostic_only": (
            actual_v3 == "BET" and v4_lean is True and hazard_lean is True
        ),
        "quoted_odds_used": False,
        "expected_value_calculated": False,
    }
