from __future__ import annotations

import os
from typing import Any

from .match_context import provider_pair, xg_or_proxy_pair


def _safe_pair(record: dict[str, Any], key: str) -> tuple[float | None, float | None]:
    try:
        if key == "xg":
            home, away, _, _ = xg_or_proxy_pair(record)
        else:
            home, away = provider_pair(record, key)
    except Exception:
        return None, None
    return (
        None if home is None else float(home),
        None if away is None else float(away),
    )


def _side_value(record: dict[str, Any], key: str, side: str) -> float | None:
    home, away = _safe_pair(record, key)
    return home if side == "home" else away


def _ratio(value: float | None, expected: float) -> float | None:
    if value is None or expected <= 0:
        return None
    return max(0.0, min(2.5, float(value) / expected))


def _weighted(values: list[tuple[float | None, float]]) -> tuple[float | None, int]:
    usable = [(value, weight) for value, weight in values if value is not None]
    if not usable:
        return None, 0
    total_weight = sum(weight for _, weight in usable)
    return sum(float(value) * weight for value, weight in usable) / total_weight, len(usable)


def _team_scoring_profile(record: dict[str, Any], side: str) -> dict[str, Any]:
    match = record.get("match") or {}
    ctx = record.get("prematch_context") or {}
    team = str(match.get("home") if side == "home" else match.get("away") or "").casefold().strip()
    rows = list(ctx.get("home_recent") if side == "home" else ctx.get("away_recent") or [])
    scored = 0
    goals = 0
    used = 0
    for row in rows:
        home_name = str(row.get("home") or "").casefold().strip()
        away_name = str(row.get("away") or "").casefold().strip()
        hs = int(row.get("home_score") or 0)
        aws = int(row.get("away_score") or 0)
        if not team:
            continue
        if home_name == team:
            gf = hs
        elif away_name == team:
            gf = aws
        else:
            continue
        used += 1
        goals += gf
        scored += int(gf > 0)
    return {
        "matches": used,
        "scored_rate": None if not used else scored / used,
        "avg_goals_for": None if not used else goals / used,
    }


def side_goal_pressure(record: dict[str, Any], side: str) -> dict[str, Any]:
    match = record.get("match") or {}
    minute = int(match.get("minute") or 0)
    progress = max(0.08, min(1.0, minute / 90.0))
    momentum = record.get("live_momentum") or {}
    prefix = "home" if side == "home" else "away"

    cumulative = {
        "xg": _side_value(record, "xg", side),
        "shots": _side_value(record, "shots", side),
        "sot": _side_value(record, "shots_on_target", side),
        "inside": _side_value(record, "shots_inside_box", side),
        "big": _side_value(record, "big_chances", side),
        "danger": _side_value(record, "dangerous_attacks", side),
        "corners": _side_value(record, "corners", side),
    }
    recent5 = {
        "xg": momentum.get(f"{prefix}_xg_last_5m"),
        "shots": momentum.get(f"{prefix}_shots_last_5m"),
        "sot": momentum.get(f"{prefix}_sot_last_5m"),
        "big": momentum.get(f"{prefix}_big_last_5m"),
        "danger": momentum.get(f"{prefix}_danger_last_5m"),
    }
    recent10 = {
        "xg": momentum.get(f"{prefix}_xg_last_10m"),
        "shots": momentum.get(f"{prefix}_shots_last_10m"),
        "sot": momentum.get(f"{prefix}_sot_last_10m"),
        "big": momentum.get(f"{prefix}_big_last_10m"),
        "danger": momentum.get(f"{prefix}_danger_last_10m"),
    }

    score, evidence = _weighted([
        (_ratio(cumulative["xg"], 1.15 * progress), 0.22),
        (_ratio(cumulative["shots"], 11.0 * progress), 0.08),
        (_ratio(cumulative["sot"], 3.5 * progress), 0.15),
        (_ratio(cumulative["inside"], 6.5 * progress), 0.12),
        (_ratio(cumulative["big"], 1.4 * progress), 0.12),
        (_ratio(cumulative["danger"], 43.0 * progress), 0.05),
        (_ratio(cumulative["corners"], 4.5 * progress), 0.03),
        (_ratio(recent5["xg"], 0.12), 0.08),
        (_ratio(recent5["shots"], 1.5), 0.05),
        (_ratio(recent5["sot"], 0.55), 0.06),
        (_ratio(recent10["xg"], 0.24), 0.02),
        (_ratio(recent10["shots"], 3.0), 0.01),
        (_ratio(recent10["sot"], 1.0), 0.01),
    ])

    recent_threat = bool(
        (recent5["xg"] is not None and float(recent5["xg"] or 0) >= 0.08)
        or (recent5["sot"] is not None and float(recent5["sot"] or 0) >= 1.0)
        or (recent5["big"] is not None and float(recent5["big"] or 0) >= 1.0)
        or (recent5["shots"] is not None and float(recent5["shots"] or 0) >= 2.0)
        or (recent5["danger"] is not None and float(recent5["danger"] or 0) >= 6.0)
    )
    quality_threat = bool(
        (cumulative["xg"] is not None and float(cumulative["xg"] or 0) >= 0.25)
        or (cumulative["sot"] is not None and float(cumulative["sot"] or 0) >= 1.0)
        or (cumulative["inside"] is not None and float(cumulative["inside"] or 0) >= 2.0)
        or (cumulative["big"] is not None and float(cumulative["big"] or 0) >= 1.0)
    )
    profile = _team_scoring_profile(record, side)
    scored_rate = profile.get("scored_rate")
    minimum = float(os.getenv("SHADOW_TEAM_GOAL_MIN_PRESSURE", "0.92"))
    min_profile = float(os.getenv("SHADOW_TEAM_GOAL_MIN_SCORED_RATE", "0.55"))
    min_evidence = int(os.getenv("SHADOW_TEAM_GOAL_MIN_EVIDENCE", "3"))
    profile_ok = scored_rate is not None and float(scored_rate) >= min_profile
    passed = bool(
        score is not None
        and score >= minimum
        and evidence >= min_evidence
        and recent_threat
        and quality_threat
        and profile_ok
    )
    blocks: list[str] = []
    if score is None or score < minimum:
        blocks.append("side_pressure_low")
    if evidence < min_evidence:
        blocks.append("side_evidence_low")
    if not recent_threat:
        blocks.append("side_no_recent_threat")
    if not quality_threat:
        blocks.append("side_no_quality_threat")
    if not profile_ok:
        blocks.append("side_prematch_scoring_profile_low")
    confidence = None if score is None else max(0.50, min(0.94, 0.50 + (score - 0.65) * 0.30))
    return {
        "side": side,
        "team": (match.get("home") if side == "home" else match.get("away")),
        "pressure_score": score,
        "minimum": minimum,
        "evidence": evidence,
        "recent_threat": recent_threat,
        "quality_threat": quality_threat,
        "prematch": profile,
        "prematch_min_scored_rate": min_profile,
        "confidence_score": confidence,
        "passed": passed,
        "blocks": blocks,
        "cumulative": cumulative,
        "recent_5m": recent5,
        "recent_10m": recent10,
    }


def analyze_btts_shadow(record: dict[str, Any]) -> dict[str, Any]:
    match = record.get("match") or {}
    minute = int(match.get("minute") or 0)
    hs = int(match.get("home_score") or 0)
    aws = int(match.get("away_score") or 0)
    home = side_goal_pressure(record, "home")
    away = side_goal_pressure(record, "away")
    blocks: list[str] = []

    if minute < 10:
        blocks.append("warmup")
    if minute > 75 or bool(match.get("is_finished")):
        blocks.append("window_closed")
    if hs > 0 and aws > 0:
        blocks.append("btts_already_won")

    target_side: str | None = None
    if hs == 0 and aws == 0:
        if not home.get("passed"):
            blocks.extend(f"home:{x}" for x in home.get("blocks") or [])
        if not away.get("passed"):
            blocks.extend(f"away:{x}" for x in away.get("blocks") or [])
        passed = not blocks and bool(home.get("passed") and away.get("passed"))
        strengths = [x for x in (home.get("confidence_score"), away.get("confidence_score")) if x is not None]
        confidence = min(strengths) if len(strengths) == 2 else None
    elif hs == 0 or aws == 0:
        target_side = "home" if hs == 0 else "away"
        target = home if target_side == "home" else away
        if not target.get("passed"):
            blocks.extend(f"{target_side}:{x}" for x in target.get("blocks") or [])
        passed = not blocks and bool(target.get("passed"))
        confidence = target.get("confidence_score")
    else:
        passed = False
        confidence = None

    return {
        "head": "both_teams_to_score",
        "name": "shadow_btts_yes",
        "passed": bool(passed),
        "confidence_score": confidence,
        "target_side": target_side,
        "home": home,
        "away": away,
        "blocks": blocks,
        "shadow_only": True,
    }


def analyze_team_goal_shadow(record: dict[str, Any]) -> dict[str, Any]:
    match = record.get("match") or {}
    minute = int(match.get("minute") or 0)
    home = side_goal_pressure(record, "home")
    away = side_goal_pressure(record, "away")
    choices = [row for row in (home, away) if row.get("passed") and row.get("confidence_score") is not None]
    choices.sort(key=lambda row: float(row.get("confidence_score") or 0), reverse=True)
    selected = choices[0] if choices else None
    blocks: list[str] = []
    if minute < 10:
        blocks.append("warmup")
    if minute > 75 or bool(match.get("is_finished")):
        blocks.append("window_closed")
    if selected is None:
        blocks.append("no_team_passed")
    return {
        "head": "team_to_score",
        "name": "shadow_team_to_score",
        "passed": bool(selected is not None and not blocks),
        "selected_side": None if selected is None else selected.get("side"),
        "team": None if selected is None else selected.get("team"),
        "confidence_score": None if selected is None else selected.get("confidence_score"),
        "pressure_score": None if selected is None else selected.get("pressure_score"),
        "home": home,
        "away": away,
        "blocks": blocks,
        "shadow_only": True,
    }
