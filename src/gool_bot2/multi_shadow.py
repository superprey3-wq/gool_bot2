from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .multi_router import RouterDecision, analyze_multi_match


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
    journal_path: Path,
    *,
    data_quality: float = 1.0,
) -> RouterDecision:
    """Run GOOL MULTI in observation-only mode and persist the full decision.

    There is intentionally no Telegram import in this module. The shadow layer
    can therefore be enabled later without any possibility of sending a live
    bet while the router is still being validated.
    """
    decision = analyze_multi_match(record.get("match") or {}, market_row, experts, data_quality=data_quality)
    append_shadow_snapshot(
        journal_path,
        decision_snapshot(record, decision, experts, data_quality=data_quality),
    )
    return decision
