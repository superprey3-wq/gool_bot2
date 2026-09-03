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


def _analysis_blocks(analysis: dict[str, Any] | None) -> list[str]:
    return [str(x) for x in ((analysis or {}).get("blocks") or []) if str(x)]


def build_expert_snapshot(
    *,
    model_result: dict[str, Any] | None = None,
    two_more_analysis: dict[str, Any] | None = None,
    home_goal_analysis: dict[str, Any] | None = None,
    away_goal_analysis: dict[str, Any] | None = None,
    btts_analysis: dict[str, Any] | None = None,
) -> dict[str, dict[str, Any]]:
    """Translate all current GOOL systems into one router-facing contract.

    A rejected GOOL system is still exposed with its probability/confidence so
    that a verified 1xBet MARKET OVERRIDE can revive a *soft* rejection. The
    router, not this adapter, owns the hard/soft distinction. This preserves the
    production behaviour where exceptional steam may override a normal WAIT
    without allowing stale/desynchronised/time-closed markets through.
    """
    model_result = model_result or {}
    out: dict[str, dict[str, Any]] = {}

    for router_key, head in (
        ("another_goal", "another_goal"),
        ("goal_before_ht", "goal_before_ht"),
    ):
        probability, source = _model_probability(model_result, head)
        if probability is None:
            continue
        analyzer = (model_result.get("gool_analyzer") or {}).get(head) or {}
        required = bool(analyzer.get("required"))
        passed = bool(analyzer.get("passed")) if required else True
        out[router_key] = {
            "probability": probability,
            "source": source,
            "passed": passed,
            "blocks": _analysis_blocks(analyzer),
            "required": required,
        }

    two_more_analysis = two_more_analysis or {}
    probability = _probability(two_more_analysis.get("confidence_score"))
    if probability is not None:
        out["two_more_goals"] = {
            "probability": probability,
            "source": str(two_more_analysis.get("confidence_source") or "gool_live:two_more_goals"),
            "passed": bool(two_more_analysis.get("passed")),
            "blocks": _analysis_blocks(two_more_analysis),
            "pressure_score": two_more_analysis.get("pressure_score"),
        }

    for router_key, analysis in (
        ("home_goal", home_goal_analysis),
        ("away_goal", away_goal_analysis),
    ):
        analysis = analysis or {}
        probability = _probability(analysis.get("probability"))
        if probability is None:
            probability = _probability(analysis.get("confidence_score"))
        if probability is None:
            continue
        out[router_key] = {
            "probability": probability,
            "source": str(analysis.get("source") or f"shadow:{router_key}"),
            "passed": bool(analysis.get("passed")),
            "blocks": _analysis_blocks(analysis),
            "pressure_score": analysis.get("pressure_score"),
        }

    btts_analysis = btts_analysis or {}
    probability = _probability(btts_analysis.get("probability"))
    if probability is None:
        probability = _probability(btts_analysis.get("confidence_score"))
    if probability is not None:
        out["btts"] = {
            "probability": probability,
            "source": str(btts_analysis.get("source") or "shadow:both_teams_to_score"),
            "passed": bool(btts_analysis.get("passed")),
            "blocks": _analysis_blocks(btts_analysis),
            "target_side": btts_analysis.get("target_side"),
        }

    return out
