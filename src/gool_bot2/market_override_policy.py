from __future__ import annotations

import os
from datetime import datetime, timezone
from typing import Any

from .value_bet_policy import ABSOLUTE_MIN_BET_ODD

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


def _primary_target_odd(out: dict[str, Any], targets: list[dict[str, Any]]) -> float | None:
    if not targets:
        return None
    head = str(out.get("head") or "")
    target: dict[str, Any] | None = None
    if head == "both_teams_to_score":
        target = next((x for x in targets if str(x.get("market") or "") == "btts_yes"), None)
    if target is None:
        target = max(targets, key=lambda x: float(x.get("weight") or 0.0))
    selection = target.get("selection") if isinstance(target, dict) else None
    if not isinstance(selection, dict) or selection.get("odd") is None:
        return None
    try:
        return float(selection.get("odd"))
    except (TypeError, ValueError):
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
    min_odd = max(
        ABSOLUTE_MIN_BET_ODD,
        float(os.getenv("XBET_OVERRIDE_MIN_ODD", str(ABSOLUTE_MIN_BET_ODD))),
    )
    level = str(out.get("level") or "NO_DATA")
    targets = [x for x in (out.get("targets") or []) if isinstance(x, dict)]
    strongest_delta = max([float(x.get("prob_delta_pp") or 0.0) for x in targets] or [float(out.get("score_pp") or 0.0)])
    strongest_moves = max([int(x.get("one_way_moves") or 0) for x in targets] or [0])
    primary_odd = _primary_target_odd(out, targets)
    fresh = age is not None and age <= max_age
    evidence = strongest_delta >= min_delta and strongest_moves >= min_moves
    price_ok = primary_odd is not None and primary_odd >= min_odd
    override = bool(
        out.get("available")
        and level in OVERRIDE_LEVELS
        and fresh
        and evidence
        and price_ok
        and level != "SCORE_DESYNC"
    )
    out.update({
        "override": override,
        "override_fresh": fresh,
        "override_evidence": evidence,
        "override_price_ok": price_ok,
        "override_primary_odd": primary_odd,
        "override_min_odd": min_odd,
        "override_min_delta_pp": min_delta,
        "override_min_moves": min_moves,
        "override_max_age_seconds": max_age,
        "strongest_delta_pp": round(strongest_delta, 2),
        "strongest_one_way_moves": strongest_moves,
    })
    if override:
        out["reason"] = f"MARKET OVERRIDE · 1xBet {level} · Δp={strongest_delta:.1f} п.п. · импульсов={strongest_moves} · кэф {primary_odd:.2f}"
    elif out.get("available") and level in OVERRIDE_LEVELS and fresh and evidence and not price_ok:
        odd_text = "нет цены" if primary_odd is None else f"кэф {primary_odd:.2f}"
        out["reason"] = f"MARKET FILTER · LOW_ODD · {odd_text} < {min_odd:.2f}"
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
