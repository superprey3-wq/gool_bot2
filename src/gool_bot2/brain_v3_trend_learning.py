from __future__ import annotations

from typing import Any, Callable


_INSTALLED = False


def install_brain_v3_trend_learning() -> None:
    """Add external-trend buckets to the existing guarded outcome learner."""
    global _INSTALLED
    if _INSTALLED:
        return
    from . import brain_v3_learning as learning

    original: Callable[[dict[str, Any]], list[str]] = learning._pattern_keys

    def pattern_keys_with_trends(brain: dict[str, Any]) -> list[str]:
        keys = list(original(brain))
        trend = brain.get("external_trends") or {}
        if isinstance(trend, dict) and bool(trend.get("available")):
            period = str(brain.get("period") or "UNK")
            label = str(trend.get("label") or "unknown")
            market = str(trend.get("market_hint") or "unknown")
            keys.append(f"external_trend:{period}:{market}:{label}")
        return list(dict.fromkeys(keys))

    learning._pattern_keys = pattern_keys_with_trends
    _INSTALLED = True
    print("GOOL_BRAIN_V3_TREND_LEARNING installed scope=historical_goal_trends", flush=True)


__all__ = ["install_brain_v3_trend_learning"]
