from __future__ import annotations

from typing import Any, Callable


_INSTALLED = False


def _number(value: Any) -> float | None:
    try:
        if value in (None, "", "-"):
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def install_brain_v3_trend_learning() -> None:
    """Add external-trend buckets and outcome diagnoses to the guarded learner."""
    global _INSTALLED
    if _INSTALLED:
        return
    from . import brain_v3_learning as learning

    original_pattern_keys: Callable[[dict[str, Any]], list[str]] = learning._pattern_keys
    original_diagnose = learning._diagnose

    def pattern_keys_with_trends(brain: dict[str, Any]) -> list[str]:
        keys = list(original_pattern_keys(brain))
        trend = brain.get("external_trends") or {}
        if isinstance(trend, dict) and bool(trend.get("available")):
            period = str(brain.get("period") or "UNK")
            label = str(trend.get("label") or "unknown")
            market = str(trend.get("market_hint") or "unknown")
            keys.append(f"external_trend:{period}:{market}:{label}")
        return list(dict.fromkeys(keys))

    def diagnose_with_trends(
        row: dict[str, Any],
        brain: dict[str, Any],
        record: dict[str, Any] | None,
    ) -> tuple[list[str], dict[str, Any]]:
        reasons, post = original_diagnose(row, brain, record)
        trend = brain.get("external_trends") or {}
        effective_pp = float(_number(trend.get("effective_adjustment_pp")) or 0.0)
        outcome = str(row.get("result") or "").lower()
        if effective_pp >= 0.75:
            if outcome == "lost":
                reasons.append("external_trend_support_did_not_convert")
            elif outcome == "won":
                reasons.append("external_trend_support_confirmed")
        elif effective_pp <= -0.75:
            if outcome == "lost":
                reasons.append("external_trend_caution_confirmed")
            elif outcome == "won":
                reasons.append("external_trend_caution_too_strong")
        return list(dict.fromkeys(reasons)), post

    learning._pattern_keys = pattern_keys_with_trends
    learning._diagnose = diagnose_with_trends
    _INSTALLED = True
    print(
        "GOOL_BRAIN_V3_TREND_LEARNING installed scope=historical_goal_trends outcome_review=on",
        flush=True,
    )


__all__ = ["install_brain_v3_trend_learning"]
