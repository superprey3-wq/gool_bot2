from __future__ import annotations

import os
from datetime import datetime, timezone
from typing import Any

OVERRIDE_LEVELS = {"STRONG_STEAM", "MULTI_MARKET_STEAM"}


def _age_seconds(captured_at: Any) -> float | None:
    if not captured_at:
        return None
    try:
        dt = datetime.fromisoformat(str(captured_at).replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return max(0.0, (datetime.now(timezone.utc) - dt.astimezone(timezone.utc)).total_seconds())
    except Exception:
        return None


def decorate_market_info(info: dict[str, Any] | None, market_row: dict[str, Any] | None) -> dict[str, Any]:
    out = dict(info or {})
    captured_at = None if market_row is None else market_row.get("captured_at")
    age = _age_seconds(captured_at)
    out["captured_at"] = captured_at
    out["age_seconds"] = None if age is None else round(age, 1)

    max_age = float(os.getenv("XBET_OVERRIDE_MAX_AGE_SECONDS", "35"))
    min_delta = float(os.getenv("XBET_OVERRIDE_MIN_DELTA_PP", "6"))
    min_moves = int(os.getenv("XBET_OVERRIDE_MIN_ONE_WAY_MOVES", "2"))
    level = str(out.get("level") or "NO_DATA")
    targets = [x for x in (out.get("targets") or []) if isinstance(x, dict)]
    strongest_delta = max([float(x.get("prob_delta_pp") or 0.0) for x in targets] or [float(out.get("score_pp") or 0.0)])
    strongest_moves = max([int(x.get("one_way_moves") or 0) for x in targets] or [0])
    fresh = age is not None and age <= max_age
    evidence = strongest_delta >= min_delta and strongest_moves >= min_moves
    override = bool(
        out.get("available")
        and level in OVERRIDE_LEVELS
        and fresh
        and evidence
        and level != "SCORE_DESYNC"
    )
    out.update({
        "override": override,
        "override_fresh": fresh,
        "override_evidence": evidence,
        "override_min_delta_pp": min_delta,
        "override_min_moves": min_moves,
        "override_max_age_seconds": max_age,
        "strongest_delta_pp": round(strongest_delta, 2),
        "strongest_one_way_moves": strongest_moves,
    })
    if override:
        out["reason"] = f"MARKET OVERRIDE · 1xBet {level} · Δp={strongest_delta:.1f} п.п. · импульсов={strongest_moves}"
    return out


def can_override_another_goal(info: dict[str, Any], probability: float | None) -> bool:
    if not info.get("override"):
        return False
    try:
        p = float(probability)
    except (TypeError, ValueError):
        return False
    if p > 1.0:
        p /= 100.0
    return p >= float(os.getenv("XBET_OVERRIDE_MIN_MODEL_PROBABILITY", "0.55"))


def hard_market_block(info: dict[str, Any]) -> bool:
    return str(info.get("level") or "") in {"SCORE_DESYNC", "NO_DATA"} or not bool(info.get("override_fresh", False))
