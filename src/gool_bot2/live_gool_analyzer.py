from __future__ import annotations

import os
from typing import Any

from .match_context import provider_pair


def _pair_total(record: dict[str, Any], key: str) -> float | None:
    home, away = provider_pair(record, key)
    if home is None or away is None:
        return None
    return float(home + away)


def _pair_side(record: dict[str, Any], key: str, side: int) -> float | None:
    home, away = provider_pair(record, key)
    value = home if side == 0 else away
    return None if value is None else float(value)


def _pressure(values: dict[str, float | None], expectations: dict[str, tuple[float, float]]) -> float | None:
    parts: list[tuple[float, float]] = []
    for key, (expected, weight) in expectations.items():
        value = values.get(key)
        if value is None or expected <= 0:
            continue
        ratio = max(0.0, min(2.5, float(value) / expected))
        parts.append((ratio, weight))
    if not parts:
        return None
    weight_sum = sum(weight for _, weight in parts)
    return float(sum(value * weight for value, weight in parts) / weight_sum)


def _confidence(score: float | None) -> float | None:
    """GOOL heuristic confidence, deliberately not presented as calibrated probability."""
    if score is None:
        return None
    return float(max(0.50, min(0.95, 0.50 + (float(score) - 0.70) * 0.30)))


def analyze_two_more_goals(record: dict[str, Any]) -> dict[str, Any]:
    """Legacy-GOOL style LIVE pressure for two additional goals from now.

    It works at any score through 75'. Recent 5m/10m momentum is weighted heavily,
    so a late acceleration can qualify even when the earlier match was quiet.
    """
    match = record.get("match") or {}
    minute = int(match.get("minute") or 0)
    output: dict[str, Any] = {
        "name": "gool_live_two_more_goals",
        "pressure_score": None,
        "minimum": float(os.getenv("GOOL_LIVE_TWO_GOAL_MIN_PRESSURE", "1.15")),
        "passed": False,
        "confidence_score": None,
    }
    if minute < 10 or minute > 75 or bool(match.get("is_finished")):
        return output

    progress = max(0.12, min(1.0, minute / 90.0))
    momentum = record.get("live_momentum") or {}
    values = {
        "xg_total": _pair_total(record, "xg"),
        "shots_total": _pair_total(record, "shots"),
        "sot_total": _pair_total(record, "shots_on_target"),
        "big_total": _pair_total(record, "big_chances"),
        "corners_total": _pair_total(record, "corners"),
        "danger_total": _pair_total(record, "dangerous_attacks"),
        "xg_5": momentum.get("xg_total_last_5m"),
        "shots_5": momentum.get("shots_total_last_5m"),
        "sot_5": momentum.get("sot_total_last_5m"),
        "big_5": momentum.get("big_total_last_5m"),
        "danger_5": momentum.get("danger_total_last_5m"),
        "xg_10": momentum.get("xg_total_last_10m"),
        "shots_10": momentum.get("shots_total_last_10m"),
        "sot_10": momentum.get("sot_total_last_10m"),
    }
    expectations = {
        "xg_total": (2.15 * progress, 0.12),
        "shots_total": (22.0 * progress, 0.07),
        "sot_total": (7.0 * progress, 0.10),
        "big_total": (3.0 * progress, 0.08),
        "corners_total": (9.0 * progress, 0.03),
        "danger_total": (86.0 * progress, 0.05),
        "xg_5": (0.34, 0.17),
        "shots_5": (3.0, 0.10),
        "sot_5": (1.2, 0.12),
        "big_5": (0.55, 0.07),
        "danger_5": (12.0, 0.03),
        "xg_10": (0.62, 0.03),
        "shots_10": (5.0, 0.02),
        "sot_10": (2.0, 0.01),
    }
    score = _pressure(values, expectations)
    output.update(values)
    output["pressure_score"] = score
    output["confidence_score"] = _confidence(score)
    output["passed"] = score is not None and float(score) >= float(output["minimum"])
    return output


def analyze_live_btts(record: dict[str, Any]) -> dict[str, Any]:
    """LIVE BTTS pressure for the team that has not scored yet."""
    match = record.get("match") or {}
    minute = int(match.get("minute") or 0)
    hs = int(match.get("home_score") or 0)
    aws = int(match.get("away_score") or 0)
    output: dict[str, Any] = {
        "name": "gool_live_btts",
        "pressure_score": None,
        "minimum": float(os.getenv("GOOL_LIVE_BTTS_MIN_PRESSURE", "1.10")),
        "passed": False,
        "confidence_score": None,
        "scoreless_side": None,
    }
    if minute < 10 or minute > 75 or bool(match.get("is_finished")):
        return output
    if (hs > 0 and aws > 0) or (hs == 0 and aws == 0):
        return output

    side = 0 if hs == 0 else 1
    output["scoreless_side"] = "home" if side == 0 else "away"
    momentum = record.get("live_momentum") or {}
    prefix = "home" if side == 0 else "away"
    values = {
        "xg": _pair_side(record, "xg", side),
        "shots": _pair_side(record, "shots", side),
        "sot": _pair_side(record, "shots_on_target", side),
        "big": _pair_side(record, "big_chances", side),
        "danger": _pair_side(record, "dangerous_attacks", side),
        "xg_5": momentum.get(f"{prefix}_xg_last_5m"),
        "shots_5": momentum.get(f"{prefix}_shots_last_5m"),
        "sot_5": momentum.get(f"{prefix}_sot_last_5m"),
        "big_5": momentum.get(f"{prefix}_big_last_5m"),
        "danger_5": momentum.get(f"{prefix}_danger_last_5m"),
        "xg_10": momentum.get(f"{prefix}_xg_last_10m"),
        "shots_10": momentum.get(f"{prefix}_shots_last_10m"),
        "sot_10": momentum.get(f"{prefix}_sot_last_10m"),
    }
    expectations = {
        "xg": (0.72, 0.12),
        "shots": (7.0, 0.07),
        "sot": (2.2, 0.10),
        "big": (0.9, 0.08),
        "danger": (30.0, 0.05),
        "xg_5": (0.22, 0.18),
        "shots_5": (2.0, 0.11),
        "sot_5": (0.8, 0.14),
        "big_5": (0.35, 0.07),
        "danger_5": (8.0, 0.03),
        "xg_10": (0.42, 0.02),
        "shots_10": (4.0, 0.02),
        "sot_10": (1.5, 0.01),
    }
    score = _pressure(values, expectations)
    output.update(values)
    output["pressure_score"] = score
    output["confidence_score"] = _confidence(score)
    output["passed"] = score is not None and float(score) >= float(output["minimum"])
    return output
