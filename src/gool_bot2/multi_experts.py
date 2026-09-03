from __future__ import annotations

from typing import Any


def _probability(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if 0.0 <= number <= 1.0:
        return number
    return None


def _model_probability(model_result: dict[str, Any], head: str) -> tuple[float | None, str]:
    for key in ("blended", "trained_probability", "direct"):
        value = _probability((model_result.get(key) or {}).get(head))
        if value is not None:
            return value, f"model:{key}:{head}"
    return None, "missing"


def build_expert_snapshot(
    *,
    model_result: dict[str, Any] | None = None,
    two_more_analysis: dict[str, Any] | None = None,
    home_goal_analysis: dict[str, Any] | None = None,
    away_goal_analysis: dict[str, Any] | None = None,
) -> dict[str, dict[str, Any]]:
    """Translate current GOOL experts into one router-facing contract.

    The adapter intentionally does not fabricate missing probabilities. A market
    family is absent until one of the existing GOOL models/analyzers can provide
    an explicit probability/confidence for that football event.
    """
    model_result = model_result or {}
    out: dict[str, dict[str, Any]] = {}

    for router_key, head in (
        ("another_goal", "another_goal"),
        ("goal_before_ht", "goal_before_ht"),
    ):
        probability, source = _model_probability(model_result, head)
        if probability is not None:
            out[router_key] = {"probability": probability, "source": source}

    two_more_analysis = two_more_analysis or {}
    probability = _probability(two_more_analysis.get("confidence_score"))
    if probability is not None:
        out["two_more_goals"] = {
            "probability": probability,
            "source": str(two_more_analysis.get("confidence_source") or "gool_live:two_more_goals"),
            "passed": bool(two_more_analysis.get("passed")),
            "pressure_score": two_more_analysis.get("pressure_score"),
        }

    for router_key, analysis in (("home_goal", home_goal_analysis), ("away_goal", away_goal_analysis)):
        analysis = analysis or {}
        probability = _probability(analysis.get("probability"))
        if probability is None:
            probability = _probability(analysis.get("confidence_score"))
        if probability is None:
            continue
        out[router_key] = {
            "probability": probability,
            "source": str(analysis.get("source") or f"shadow:{router_key}"),
            "passed": bool(analysis.get("passed", True)),
            "pressure_score": analysis.get("pressure_score"),
        }

    return out
