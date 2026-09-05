from __future__ import annotations

from typing import Any

from .xbet_prematch_market import find_prematch_market


def _number(value: Any) -> float | None:
    try:
        if value in (None, "", "-"):
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def apply_true_prematch_market(record: dict[str, Any], experts: dict[str, Any]) -> dict[str, Any] | None:
    """Replace near-kickoff fallback with a verified pre-kickoff LineFeed snapshot.

    `apply_match_intelligence` may have used the first <=5' live snapshot as a
    conservative fallback. When the background LineFeed collector has a real
    prematch market for the same teams, this function recalculates only the small
    kickoff-market contribution and upgrades the suitability component. It never
    changes PASS/HARD_NO state by itself.
    """
    match = record.get("match") or {}
    home = str(match.get("home") or "").strip()
    away = str(match.get("away") or "").strip()
    if not home or not away:
        return None
    row = find_prematch_market(home, away)
    if not row:
        return None

    snapshot = {
        "available": True,
        "quality": "prematch_linefeed",
        "usable_as_kickoff_prior": True,
        "source": row.get("source") or "1xbet:LineFeed",
        "event_id": row.get("event_id"),
        "captured_at": row.get("captured_at"),
        "first_captured_at": row.get("first_captured_at"),
        "match_score": row.get("match_score"),
        "1x2": dict(row.get("match_1x2") or {}),
        "main_total": dict(row.get("main_total") or {}),
        "match_totals": [dict(x) for x in (row.get("match_totals") or []) if isinstance(x, dict)],
    }
    record["xbet_prematch_market"] = snapshot

    intel = record.get("match_intelligence") or {}
    if not isinstance(intel, dict):
        return snapshot
    old_kickoff = dict(intel.get("kickoff_market") or {})
    intel["kickoff_market"] = snapshot
    adjustment = intel.get("probability_adjustment") or {}
    strategy = str(adjustment.get("strategy") or "")
    expert = experts.get(strategy) if strategy else None
    before = _number(adjustment.get("before"))
    if isinstance(expert, dict) and before is not None:
        fair_over = _number((snapshot.get("main_total") or {}).get("fair_over"))
        new_kickoff_pp = 0.0
        if fair_over is not None:
            new_kickoff_pp = max(-1.5, min(1.5, (fair_over - 0.5) * 8.0))
        hazard_pp = float(adjustment.get("hazard_pp") or 0.0)
        chance_pp = float(adjustment.get("chance_quality_pp") or 0.0)
        total_pp = max(-4.0, min(4.0, hazard_pp + chance_pp + new_kickoff_pp))
        adjusted = max(0.01, min(0.99, float(before) + total_pp / 100.0))
        expert["probability"] = round(adjusted, 4)
        adjustment["kickoff_total_pp"] = round(new_kickoff_pp, 2)
        adjustment["total_pp"] = round(total_pp, 2)
        adjustment["after"] = round(adjusted, 4)
        adjustment["kickoff_source"] = "prematch_linefeed"
        diagnostics = dict(expert.get("diagnostics") or {})
        mi = dict(diagnostics.get("match_intelligence") or {})
        mi["kickoff_prior_used"] = True
        mi["kickoff_prior_source"] = "prematch_linefeed"
        mi["delta_pp"] = round(total_pp, 2)
        mi["probability_after"] = round(adjusted, 4)
        diagnostics["match_intelligence"] = mi
        expert["diagnostics"] = diagnostics

    suitability = intel.get("suitability") or {}
    components = suitability.get("components") or {}
    if isinstance(components, dict):
        old_value = float(components.get("kickoff") or (0.92 if old_kickoff.get("usable_as_kickoff_prior") else 0.55))
        components["kickoff"] = 0.92
        score = float(suitability.get("score") or 0.0) + (0.92 - old_value) * 0.05
        suitability["score"] = round(max(0.0, min(1.0, score)), 4)
        suitability["kickoff_source"] = "prematch_linefeed"

    record["match_intelligence"] = intel
    return snapshot


__all__ = ["apply_true_prematch_market"]
