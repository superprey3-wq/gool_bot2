"""Hockey PREMATCH EV challenger: shadow-only pricing and diagnostics.

Unlike an edge based on P(win | not push), expected net return accounts
for the full probability of win, push and loss:
    EV_per_unit = P(win) * (decimal_odds - 1) - P(loss)

The input goal means come from the existing Hockey V2 model. This module
does NOT claim an independently calibrated Hockey V3 probability model;
it highlights where the existing model and bookmaker prices disagree.
Never creates, vetoes, delivers or settles a public bet.
"""
from __future__ import annotations

import math
from typing import Any

from .hockey_brain_v2 import (
    _data_quality,
    _scope_goal_share,
    _side_probabilities,
    prematch_lambdas,
)
from .segment_memory import segment_prior


def _finite(value: Any) -> float | None:
    try:
        val = float(value)
    except (TypeError, ValueError):
        return None
    return val if math.isfinite(val) else None


def evaluate_hockey_prematch_ev_shadow(
    lane: dict[str, Any],
    features: dict[str, Any],
    league: str,
    segment_memory: dict[str, Any] | None = None,
    active_signal: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Evaluate both sides of a scoped goals-total with the true push-aware EV."""
    scope = str(lane.get("scope") or "FULL_MATCH")
    family = str(lane.get("market_family") or "")
    base: dict[str, Any] = {
        "model": "hockey_prematch_ev_shadow",
        "status": "WAIT",
        "scope": scope,
        "market_family": family,
        "active_v2_direction": active_signal.get("direction") if active_signal else None,
    }
    if family not in {"match_total", "home_total", "away_total"}:
        return {**base, "reason": "only_scoped_goals_totals_supported"}
    if scope not in {"FULL_MATCH", "PERIOD_1", "PERIOD_2", "PERIOD_3"}:
        return {**base, "reason": "unknown_goals_scope"}
    line = _finite(lane.get("line"))
    if line is None or line < 0:
        return {**base, "reason": "line_unavailable"}

    quality = _data_quality(features)
    if quality < 0.55:
        return {**base, "reason": "insufficient_team_data", "data_quality": round(quality, 3)}
    full_home, full_away, league_prior = prematch_lambdas(features, league)
    share = _scope_goal_share(scope)
    home, away = full_home * share, full_away * share
    memory = dict(segment_memory or {})
    memory_quality = _finite(memory.get("quality")) or 0.0
    if scope.startswith("PERIOD_") and memory_quality >= 0.25:
        prior = segment_prior(memory, scope)
        hm, am = _finite(prior.get("expected_home")), _finite(prior.get("expected_away"))
        if hm is not None and am is not None and hm >= 0 and am >= 0:
            weight = max(0.18, min(0.60, 0.18 + memory_quality * 0.44))
            home = home * (1 - weight) + hm * weight
            away = away * (1 - weight) + am * weight

    lam = home + away if family == "match_total" else home if family == "home_total" else away
    rows: list[dict[str, Any]] = []
    for direction in ("over", "under"):
        odd = _finite(lane.get(direction))
        if odd is None or odd < 1.40:
            continue
        pw, pp, pl = _side_probabilities(lam, line, direction)
        if not all(math.isfinite(v) for v in (pw, pp, pl)):
            continue
        ev = pw * (odd - 1.0) - pl
        # Fair break-even win probability conditional on a decisive result.
        # With push, 1/odd applies to P(win)/(P(win)+P(loss)), not raw P(win).
        decisive_p = pw / (pw + pl) if pw + pl > 0 else 0.0
        rows.append({
            "direction": direction,
            "odd": round(odd, 4),
            "win_probability_uncalibrated": round(pw, 6),
            "push_probability": round(pp, 6),
            "loss_probability_uncalibrated": round(pl, 6),
            "conditional_win_probability_uncalibrated": round(decisive_p, 6),
            "break_even_probability": round(1.0 / odd, 6),
            "expected_value_units_uncalibrated": round(ev, 6),
        })
    if not rows:
        return {**base, "reason": "no_eligible_price"}
    best = max(rows, key=lambda item: item["expected_value_units_uncalibrated"])
    ev = float(best["expected_value_units_uncalibrated"])
    return {
        **base,
        "status": "PASS_SHADOW" if ev >= 0.05 else "WAIT",
        "reason": "positive_model_ev_not_calibrated" if ev >= 0.05 else "no_model_ev_margin",
        "direction": best["direction"],
        "active_v2_agrees": (
            best["direction"] == base["active_v2_direction"]
            if base["active_v2_direction"] else None
        ),
        "data_quality": round(quality, 3),
        "league_baseline": round(league_prior, 3),
        "lambda_scoped": round(lam, 4),
        "line": line,
        "alternatives": rows,
        "best_expected_value_units_uncalibrated": round(ev, 6),
        "probabilities_calibrated": False,
    }
