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


def _number(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _model_probability(model_result: dict[str, Any], head: str) -> tuple[float | None, str]:
    for key in ("blended", "trained_probability", "direct"):
        value = _probability((model_result.get(key) or {}).get(head))
        if value is not None:
            return value, f"model:{key}:{head}"
    return None, "missing"


def _analysis_blocks(analysis: dict[str, Any] | None) -> list[str]:
    return [str(x) for x in ((analysis or {}).get("blocks") or []) if str(x)]


def _model_diagnostics(analyzer: dict[str, Any]) -> dict[str, Any]:
    details = analyzer.get("details") or {}
    prematch = details.get("prematch") or {}
    live = details.get("live") or {}
    home_form = prematch.get("home_form") or {}
    away_form = prematch.get("away_form") or {}
    return {
        "prematch": {
            "available": bool(prematch),
            "passed": bool(prematch.get("passed")) if prematch else None,
            "score": _number(prematch.get("score")),
            "minimum": _number(prematch.get("minimum")),
            "combined_avg_total": _number(prematch.get("combined_avg_total")),
            "home_matches": int(home_form.get("matches") or 0),
            "away_matches": int(away_form.get("matches") or 0),
            "home_avg_total": _number(home_form.get("avg_total")),
            "away_avg_total": _number(away_form.get("avg_total")),
            "blocks": _analysis_blocks(prematch),
        },
        "live": {
            "available": bool(live),
            "passed": bool(live.get("passed")) if live else None,
            "pressure": _number(live.get("combined_pressure") or live.get("pressure_score")),
            "cumulative": _number(live.get("cumulative_pressure")),
            "pressure_5m": _number(live.get("pressure_5m")),
            "pressure_10m": _number(live.get("pressure_10m")),
            "minimum_cumulative": _number(live.get("minimum_cumulative")),
            "minimum_5m": _number(live.get("minimum_5m")),
            "minimum_10m": _number(live.get("minimum_10m")),
            "direct_threat": live.get("direct_threat"),
            "quality_threat": live.get("quality_threat"),
            "xg_source": live.get("xg_source"),
            "blocks": _analysis_blocks(live),
        },
    }


def _model_blocks(analyzer: dict[str, Any]) -> list[str]:
    blocks = _analysis_blocks(analyzer)
    details = analyzer.get("details") or {}
    for section in ("prematch", "live"):
        for block in _analysis_blocks(details.get(section) or {}):
            blocks.append(f"{section}:{block}")
    return blocks


def _two_more_blocks(analysis: dict[str, Any]) -> list[str]:
    blocks = _analysis_blocks(analysis)
    hard = str(analysis.get("hard_time_block") or "").strip()
    if hard:
        blocks.append(hard)
    pressure = _number(analysis.get("pressure_score"))
    minimum = _number(analysis.get("minimum"))
    if pressure is not None and minimum is not None and pressure < minimum:
        blocks.append(f"pressure={pressure:.2f}<{minimum:.2f}")
    if analysis.get("recent_ready") is False:
        blocks.append("recent_not_ready")
    if analysis.get("recent_threat") is False:
        blocks.append("no_recent_threat")
    if analysis.get("quality_threat") is False:
        blocks.append("no_quality_threat")
    return list(dict.fromkeys(blocks))


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
    router, not this adapter, owns the hard/soft distinction. Diagnostic fields
    deliberately distinguish calibrated model probabilities from GOOL heuristic
    confidence so the Telegram analysis view does not present every 0.50 floor
    as a literal 50% scoring probability.
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
            "metric": "probability",
            "source": source,
            "passed": passed,
            "blocks": _model_blocks(analyzer),
            "required": required,
            "diagnostics": _model_diagnostics(analyzer),
        }

    two_more_analysis = two_more_analysis or {}
    probability = _probability(two_more_analysis.get("confidence_score"))
    if probability is not None:
        out["two_more_goals"] = {
            "probability": probability,
            "metric": "confidence",
            "source": str(two_more_analysis.get("confidence_source") or "gool_live:two_more_goals"),
            "passed": bool(two_more_analysis.get("passed")),
            "blocks": _two_more_blocks(two_more_analysis),
            "pressure_score": two_more_analysis.get("pressure_score"),
            "minimum": two_more_analysis.get("minimum"),
            "recent_ready": two_more_analysis.get("recent_ready"),
            "recent_threat": two_more_analysis.get("recent_threat"),
            "quality_threat": two_more_analysis.get("quality_threat"),
        }

    for router_key, analysis in (
        ("home_goal", home_goal_analysis),
        ("away_goal", away_goal_analysis),
    ):
        analysis = analysis or {}
        probability = _probability(analysis.get("probability"))
        metric = "probability"
        if probability is None:
            probability = _probability(analysis.get("confidence_score"))
            metric = "confidence"
        if probability is None:
            continue
        out[router_key] = {
            "probability": probability,
            "metric": metric,
            "source": str(analysis.get("source") or f"shadow:{router_key}"),
            "passed": bool(analysis.get("passed")),
            "blocks": _analysis_blocks(analysis),
            "pressure_score": analysis.get("pressure_score"),
            "minimum": analysis.get("minimum"),
            "evidence": analysis.get("evidence"),
            "recent_threat": analysis.get("recent_threat"),
            "quality_threat": analysis.get("quality_threat"),
            "prematch": analysis.get("prematch"),
        }

    btts_analysis = btts_analysis or {}
    probability = _probability(btts_analysis.get("probability"))
    metric = "probability"
    if probability is None:
        probability = _probability(btts_analysis.get("confidence_score"))
        metric = "confidence"
    if probability is not None:
        out["btts"] = {
            "probability": probability,
            "metric": metric,
            "source": str(btts_analysis.get("source") or "shadow:both_teams_to_score"),
            "passed": bool(btts_analysis.get("passed")),
            "blocks": _analysis_blocks(btts_analysis),
            "target_side": btts_analysis.get("target_side"),
        }

    return out
