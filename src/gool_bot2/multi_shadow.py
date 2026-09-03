from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .match_context import provider_count, provider_pair, xg_or_proxy_pair
from .multi_router import RouterDecision, analyze_multi_match


def _pair_total(record: dict[str, Any], key: str) -> float | None:
    try:
        home, away = provider_pair(record, key)
    except Exception:
        return None
    if home is None or away is None:
        return None
    return float(home) + float(away)


def context_snapshot(record: dict[str, Any]) -> dict[str, Any]:
    prematch = record.get("prematch_context") or {}
    momentum = record.get("live_momentum") or {}
    try:
        xh, xa, xg_source, xg_evidence = xg_or_proxy_pair(record)
    except Exception:
        xh = xa = None
        xg_source = "unavailable"
        xg_evidence = 0
    return {
        "prematch": {
            "available": bool(prematch),
            "source": prematch.get("source"),
            "sources": list(prematch.get("sources") or []),
            "home_recent": len(prematch.get("home_recent") or []),
            "away_recent": len(prematch.get("away_recent") or []),
            "home_at_home": len(prematch.get("home_at_home") or []),
            "away_away": len(prematch.get("away_away") or []),
            "h2h": len(prematch.get("h2h") or []),
        },
        "live": {
            "providers": provider_count(record),
            "xg_source": xg_source,
            "xg_evidence": xg_evidence,
            "xg_home": xh,
            "xg_away": xa,
            "xg_total": None if xh is None or xa is None else float(xh) + float(xa),
            "shots_total": _pair_total(record, "shots"),
            "sot_total": _pair_total(record, "shots_on_target"),
            "big_chances_total": _pair_total(record, "big_chances"),
            "dangerous_attacks_total": _pair_total(record, "dangerous_attacks"),
            "xg_5m": momentum.get("xg_total_last_5m"),
            "shots_5m": momentum.get("shots_total_last_5m"),
            "sot_5m": momentum.get("sot_total_last_5m"),
            "xg_10m": momentum.get("xg_total_last_10m"),
            "shots_10m": momentum.get("shots_total_last_10m"),
            "sot_10m": momentum.get("sot_total_last_10m"),
        },
        "prefilter": dict(record.get("prefilter") or {}),
    }


def decision_snapshot(
    record: dict[str, Any],
    decision: RouterDecision,
    experts: dict[str, Any],
    *,
    data_quality: float,
) -> dict[str, Any]:
    match = record.get("match") or {}
    return {
        "created_at": datetime.now(timezone.utc).isoformat(),
        "mode": "shadow",
        "match_id": str(match.get("flashscore_event_id") or ""),
        "home": match.get("home"),
        "away": match.get("away"),
        "league": match.get("league"),
        "minute": int(match.get("minute") or 0),
        "score": [int(match.get("home_score") or 0), int(match.get("away_score") or 0)],
        "data_quality": float(data_quality),
        "context": context_snapshot(record),
        "experts": experts,
        "router": decision.to_dict(),
    }


def append_shadow_snapshot(path: Path, snapshot: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(snapshot, ensure_ascii=False, separators=(",", ":")) + "\n")


def analyze_and_record(
    record: dict[str, Any],
    market_row: dict[str, Any] | None,
    experts: dict[str, Any],
    analysis_path: Path,
    *,
    data_quality: float = 1.0,
) -> RouterDecision:
    """Run GOOL MULTI and append the full per-snapshot analysis row.

    The persistent bet journal is intentionally separate: analysis contains
    every BET/WAIT decision, while the journal contains only unique selected
    BEST BET entries and their settlements.
    """
    decision = analyze_multi_match(record.get("match") or {}, market_row, experts, data_quality=data_quality)
    append_shadow_snapshot(
        analysis_path,
        decision_snapshot(record, decision, experts, data_quality=data_quality),
    )
    return decision
