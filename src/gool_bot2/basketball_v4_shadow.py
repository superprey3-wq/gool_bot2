"""Basketball V4 evaluation lane: compare dynamic V2 against Historical V3.

SHADOW ONLY: never selects, vetoes or publishes a Telegram bet.
Use the *same* bookmaker quote and Flashscore snapshot already priced by GOOL.
Predicted probabilities are not calibrated; no automated EV assertions.
"""
from __future__ import annotations

import math
from typing import Any

from .basketball_brain_v2 import live_signal as basketball_v2_live_signal


def assess_basketball_v4_live(
    brain: dict[str, Any],
    lane: dict[str, Any],
    historical_signal: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Return compact, JSON-safe challenger evidence for live quarter markets."""
    scope = str(lane.get("scope") or "")
    family = str(lane.get("market_family") or "")
    base: dict[str, Any] = {
        "model": "basketball_v4_shadow_v2_dynamic",
        "state": "WAIT",
        "scope": scope,
        "market_family": family,
        "historical_direction": (
            str(historical_signal.get("direction") or "") if historical_signal else None
        ),
        "historical_probability_proxy": (
            historical_signal.get("model_probability") if historical_signal else None
        ),
    }
    if (
        not scope.startswith("QUARTER_")
        or scope != str(brain.get("scope") or "")
        or family != "match_total"
    ):
        return {**base, "reason": "not_verified_current_quarter"}
    if bool(brain.get("break_transition")) or not bool(brain.get("segment_score_verified")):
        return {**base, "reason": "score_or_transition_unverified"}
    if str(brain.get("brain_state") or "") not in {"PASS", "BORDERLINE"}:
        return {**base, "reason": "dynamic_brain_not_ready"}
    try:
        quote = float(lane.get("line"))
        elapsed = float(lane.get("clock_seconds"))
        if not math.isfinite(quote) or not math.isfinite(elapsed):
            return {**base, "reason": "non_finite_quote_or_clock"}
    except (TypeError, ValueError):
        return {**base, "reason": "quote_or_clock_unavailable"}
    try:
        candidate = basketball_v2_live_signal(brain, lane)
    except (KeyError, IndexError, TypeError, ValueError, ZeroDivisionError) as exc:
        return {**base, "reason": "dynamic_error_" + type(exc).__name__}
    if candidate is None:
        return {**base, "reason": "dynamic_v2_gate_wait"}
    direction = str(candidate.get("direction") or "")
    p = candidate.get("model_probability")
    return {
        **base,
        "state": "PASS",
        "reason": "dynamic_v2_qualified",
        "direction": direction,
        "model_probability_uncalibrated": p,
        "market_probability": candidate.get("market_probability"),
        "projected_total": candidate.get("projected_total"),
        "possession_source": candidate.get("possession_source"),
        "projection_clock_source": candidate.get("projection_clock_source"),
        "agreement_with_v3": (
            direction == base["historical_direction"]
            if base["historical_direction"] else None
        ),
    }
