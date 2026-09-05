from __future__ import annotations

from typing import Any


def _number(value: Any) -> float | None:
    try:
        if value in (None, "", "-"):
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _summary(record: dict[str, Any]) -> dict[str, Any] | None:
    meta = (((record.get("providers") or {}).get("fotmob") or {}).get("meta") or {})
    lineup = meta.get("lineup_summary")
    return dict(lineup) if isinstance(lineup, dict) and lineup.get("available") else None


def _history_multiplier(total_unavailable: int) -> float:
    if total_unavailable >= 8:
        return 0.45
    if total_unavailable >= 6:
        return 0.60
    if total_unavailable >= 4:
        return 0.75
    return 1.0


def _lineup_component(total_starters: int, total_unavailable: int) -> float:
    if total_starters >= 20 and total_unavailable <= 2:
        return 0.92
    if total_starters >= 18 and total_unavailable <= 4:
        return 0.80
    if total_unavailable >= 8:
        return 0.48
    if total_unavailable >= 5:
        return 0.60
    return 0.70


def apply_lineup_context(record: dict[str, Any], experts: dict[str, Any]) -> dict[str, Any] | None:
    """Use confirmed lineups as uncertainty control, not as a goal trigger.

    When many players are unavailable, the historical half profile is less
    representative of today's XI. We therefore shrink only the history-driven
    probability contribution. Live football, current score and market evidence
    remain untouched.
    """
    lineup = _summary(record)
    if lineup is None:
        return None

    home = dict(lineup.get("home") or {})
    away = dict(lineup.get("away") or {})
    total_unavailable = int(lineup.get("total_unavailable") or 0)
    total_starters = int(lineup.get("total_starters") or 0)
    multiplier = _history_multiplier(total_unavailable)
    component = _lineup_component(total_starters, total_unavailable)
    context = {
        "available": True,
        "source": "fotmob:lineup",
        "lineup_type": lineup.get("lineup_type"),
        "home": home,
        "away": away,
        "total_starters": total_starters,
        "total_unavailable": total_unavailable,
        "history_multiplier": multiplier,
        "suitability_component": component,
        "probability_delta_pp": 0.0,
    }

    intel = record.get("match_intelligence") or {}
    if isinstance(intel, dict):
        old_lineup = dict(intel.get("lineup") or {})
        intel["lineup"] = context
        suitability = intel.get("suitability") or {}
        components = suitability.get("components") or {}
        if isinstance(components, dict):
            old_component = float(components.get("lineup") or (0.82 if old_lineup.get("available") else 0.55))
            components["lineup"] = component
            score = float(suitability.get("score") or 0.0) + (component - old_component) * 0.03
            suitability["score"] = round(max(0.0, min(1.0, score)), 4)
            # Six unavailable players already shrink the historical contribution
            # materially (to 60%), but with a complete starting XI this is still
            # medium uncertainty rather than a high-risk lineup state. Reserve
            # HIGH for the severe >=8 unavailable bucket (45% history weight).
            suitability["lineup_risk"] = "high" if multiplier < 0.60 else ("medium" if multiplier < 1.0 else "normal")

    if multiplier < 1.0:
        minute = int((record.get("match") or {}).get("minute") or 0)
        strategy = "goal_before_ht" if 1 <= minute <= 30 else ("another_goal" if 46 <= minute <= 75 else None)
        expert = experts.get(strategy) if strategy else None
        if isinstance(expert, dict):
            diagnostics = dict(expert.get("diagnostics") or {})
            half = dict(diagnostics.get("half_prematch_prior") or {})
            live_before = _number(half.get("live_probability_before"))
            history_after = _number(half.get("probability_after"))
            current = _number(expert.get("probability"))
            if live_before is not None and history_after is not None and current is not None:
                history_delta = history_after - live_before
                remove = history_delta * (1.0 - multiplier)
                adjusted = max(0.01, min(0.99, current - remove))
                expert["probability"] = round(adjusted, 4)
                context["probability_delta_pp"] = round((adjusted - current) * 100.0, 2)
                context["history_delta_before_shrink_pp"] = round(history_delta * 100.0, 2)
                half["lineup_history_multiplier"] = multiplier
                half["probability_after_lineup_shrink"] = round(adjusted, 4)
                diagnostics["half_prematch_prior"] = half
                expert["diagnostics"] = diagnostics
                # Match-intelligence diagnostics must reflect the actual final
                # expert probability used by the router.
                adjustment = intel.get("probability_adjustment") or {}
                if isinstance(adjustment, dict):
                    adjustment["after_lineup"] = round(adjusted, 4)
                    adjustment["lineup_pp"] = context["probability_delta_pp"]

    record["match_intelligence"] = intel
    return context


__all__ = ["apply_lineup_context"]
