from __future__ import annotations

import os
from typing import Any


_DYNAMIC_BLOCKS = {"brain_v3_probability_below_bet", "brain_v3_wait_confirmation"}
_ALLOWED_STATES = {
    "HOME_SIEGE",
    "AWAY_SIEGE",
    "END_TO_END",
    "HOME_PRESSURE",
    "AWAY_PRESSURE",
    "HOME_BUILDING",
    "AWAY_BUILDING",
}


def _number(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return float(default)


def _env_float(name: str, default: float) -> float:
    return _number(os.getenv(name), default)


def _sync_expert(experts: dict[str, Any], decision: dict[str, Any]) -> None:
    strategy = str(decision.get("strategy") or "")
    expert = experts.get(strategy) if strategy else None
    if not isinstance(expert, dict) or not str(expert.get("source") or "").startswith("brain_v3:"):
        return
    status = str(decision.get("status") or "WATCH")
    expert["probability"] = round(_number(decision.get("probability"), 0.50), 4)
    expert["passed"] = status == "BET"
    expert["state"] = "PASS" if status == "BET" else "BORDERLINE"
    expert["blocks"] = list(decision.get("blocks") or [])
    diagnostics = dict(expert.get("diagnostics") or {})
    diagnostics["brain_v3"] = decision
    expert["diagnostics"] = diagnostics


def apply_strong_live_entry(
    record: dict[str, Any],
    experts: dict[str, Any],
    decision: dict[str, Any],
) -> dict[str, Any]:
    """Allow a narrow LIVE-only path just below the ordinary 70% threshold.

    The relaxed path is intentionally football-driven and does not use bookmaker
    prices. It requires a complete LIVE foundation, sustained pressure, enough
    remaining goal mass, and at least two independent recent-attacking signals.
    Late matches, large leads, falling pressure and weak-quality feeds keep the
    normal stricter threshold.
    """
    if not isinstance(decision, dict) or not bool(decision.get("active")):
        return decision

    probability = _number(decision.get("probability"), 0.0)
    live_probability = _number(decision.get("live_probability"), 0.0)
    expected_remaining = _number(decision.get("expected_goals_remaining"), 0.0)
    quality = _number(decision.get("data_quality"), 0.0)
    pressure_index = _number(decision.get("pressure_index"), 0.0)
    minute = int(_number(decision.get("minute"), 0.0))
    period = str(decision.get("period") or "")
    state = str(decision.get("match_state") or "NO_DATA")
    score = list(decision.get("score") or [0, 0])
    try:
        margin = abs(int(score[0]) - int(score[1]))
    except (TypeError, ValueError, IndexError):
        margin = 0

    recent = dict(decision.get("recent") or {})
    xg5 = _number(recent.get("xg5"), -1.0)
    sot5 = _number(recent.get("sot5"), -1.0)
    big5 = _number(recent.get("big5"), -1.0)

    evidence: list[str] = []
    if xg5 >= _env_float("GOOL_BRAIN_V3_STRONG_LIVE_XG5_MIN", 0.20):
        evidence.append("xg5")
    if sot5 >= _env_float("GOOL_BRAIN_V3_STRONG_LIVE_SOT5_MIN", 2.0):
        evidence.append("sot5")
    if big5 >= _env_float("GOOL_BRAIN_V3_STRONG_LIVE_BIG5_MIN", 1.0):
        evidence.append("big5")
    if pressure_index >= _env_float("GOOL_BRAIN_V3_STRONG_LIVE_PRESSURE_MIN", 0.55):
        evidence.append("pressure")

    base_bet_min = _number(decision.get("bet_min"), 0.70)
    strong_floor = max(0.60, min(0.69, _env_float("GOOL_BRAIN_V3_STRONG_LIVE_BET_MIN", 0.64)))
    pure_live_floor = max(0.58, min(0.69, _env_float("GOOL_BRAIN_V3_STRONG_LIVE_PURE_MIN", 0.62)))
    quality_floor = max(0.45, min(0.80, _env_float("GOOL_BRAIN_V3_STRONG_LIVE_QUALITY_MIN", 0.55)))
    expected_floor = _env_float(
        "GOOL_BRAIN_V3_STRONG_LIVE_XG_REMAIN_MIN_1H" if period == "1H" else "GOOL_BRAIN_V3_STRONG_LIVE_XG_REMAIN_MIN_2H",
        0.95 if period == "1H" else 1.00,
    )
    evidence_min = max(2, min(4, int(_env_float("GOOL_BRAIN_V3_STRONG_LIVE_EVIDENCE_MIN", 2))))

    # Stay conservative near the end of either entry window.
    if period == "1H" and minute >= 30:
        strong_floor = max(strong_floor, 0.66)
        pure_live_floor = max(pure_live_floor, 0.64)
    if period == "2H" and minute >= 65:
        strong_floor = max(strong_floor, 0.66)
        pure_live_floor = max(pure_live_floor, 0.64)
        expected_floor = max(expected_floor, 1.05)

    home_pressure = _number(decision.get("home_pressure"), 0.0)
    away_pressure = _number(decision.get("away_pressure"), 0.0)
    strongest_trend = str(
        decision.get("home_trend") if home_pressure >= away_pressure else decision.get("away_trend")
        or "WARMING"
    )

    qualified = bool(
        period in {"1H", "2H"}
        and minute < 70
        and margin < 3
        and state in _ALLOWED_STATES
        and bool(decision.get("live_foundation"))
        and bool(decision.get("sustained_pressure"))
        and strongest_trend != "FALLING"
        and not bool(decision.get("late_draw"))
        and quality >= quality_floor
        and probability >= strong_floor
        and live_probability >= pure_live_floor
        and expected_remaining >= expected_floor
        and len(evidence) >= evidence_min
    )
    activated = bool(qualified and probability < base_bet_min)

    decision["base_bet_min"] = round(base_bet_min, 4)
    decision["strong_live"] = {
        "version": 1,
        "qualified": qualified,
        "activated": activated,
        "bet_floor": round(strong_floor, 4),
        "pure_live_floor": round(pure_live_floor, 4),
        "quality_floor": round(quality_floor, 4),
        "expected_remaining_floor": round(expected_floor, 4),
        "evidence_required": evidence_min,
        "evidence": evidence,
        "evidence_count": len(evidence),
        "strongest_trend": strongest_trend,
        "bookmaker_required": False,
    }
    decision.setdefault("entry_mode", "STANDARD")

    if not activated:
        return decision

    decision["entry_mode"] = "STRONG_LIVE"
    decision["bet_min"] = round(strong_floor, 4)
    decision["status"] = "BET"
    decision["blocks"] = [
        str(block)
        for block in list(decision.get("blocks") or [])
        if str(block) not in _DYNAMIC_BLOCKS
    ]
    thoughts = list(decision.get("thoughts") or [])
    thoughts.append(
        f"strong_live={probability:.3f}>={strong_floor:.3f} "
        f"live={live_probability:.3f} evidence={'+'.join(evidence)}"
    )
    decision["thoughts"] = thoughts
    record["brain_v3_decision"] = decision
    _sync_expert(experts, decision)
    return decision


__all__ = ["apply_strong_live_entry"]
