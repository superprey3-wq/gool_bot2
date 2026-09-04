from __future__ import annotations

import os
from typing import Any

from .shadow_markets import side_goal_pressure


PASS = "PASS"
BORDERLINE = "BORDERLINE"
HARD_NO = "HARD_NO"
NO_DATA = "NO_DATA"


def _f(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        return default


def _clamp(value: float, lo: float = 0.0, hi: float = 1.0) -> float:
    return max(lo, min(hi, float(value)))


def _prob(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if number > 1.0:
        number /= 100.0
    return _clamp(number) if 0.0 <= number <= 1.0 else None


def _model_prior(model_result: dict[str, Any], head: str) -> float | None:
    """Use trained models only as a weak prior, never as the final GOOL decision."""
    for bucket in ("trained_probability", "direct"):
        value = _prob((model_result.get(bucket) or {}).get(head))
        if value is not None:
            return value
    return None


def _profile_strength(profile: dict[str, Any]) -> float | None:
    scored = _prob(profile.get("scored_rate"))
    try:
        avg_for = float(profile.get("avg_goals_for"))
    except (TypeError, ValueError):
        avg_for = None
    pieces: list[tuple[float, float]] = []
    if scored is not None:
        pieces.append((scored, 0.68))
    if avg_for is not None:
        pieces.append((_clamp(avg_for / 1.65), 0.32))
    if not pieces:
        return None
    total = sum(weight for _, weight in pieces)
    return sum(value * weight for value, weight in pieces) / total


def _red_count(record: dict[str, Any], side: str) -> int:
    cards = record.get("cards") or {}
    try:
        return max(0, int(cards.get(f"{side}_red") or 0))
    except (TypeError, ValueError):
        return 0


def _side_state(record: dict[str, Any], side: str) -> dict[str, Any]:
    raw = dict(side_goal_pressure(record, side) or {})
    match = record.get("match") or {}
    minute = int(match.get("minute") or 0)
    finished = bool(match.get("is_finished"))
    confidence = _prob(raw.get("confidence_score"))
    pressure = raw.get("pressure_score")
    evidence = int(raw.get("evidence") or 0)
    recent = bool(raw.get("recent_threat"))
    quality = bool(raw.get("quality_threat"))
    profile = dict(raw.get("prematch") or {})
    profile_strength = _profile_strength(profile)

    if confidence is None and pressure is not None:
        try:
            confidence = _clamp(0.50 + (float(pressure) - 0.65) * 0.30, 0.45, 0.94)
        except (TypeError, ValueError):
            confidence = None

    parts: list[tuple[float, float]] = []
    if confidence is not None:
        parts.append((confidence, 0.70))
    if profile_strength is not None:
        parts.append((profile_strength, 0.18))
    threat_strength = 0.42 + (0.30 if recent else 0.0) + (0.28 if quality else 0.0)
    parts.append((_clamp(threat_strength), 0.12))

    if parts:
        total_weight = sum(weight for _, weight in parts)
        strength = sum(value * weight for value, weight in parts) / total_weight
    else:
        strength = 0.50

    hs = int(match.get("home_score") or 0)
    aws = int(match.get("away_score") or 0)
    own_score, opp_score = (hs, aws) if side == "home" else (aws, hs)
    if own_score < opp_score:
        strength += 0.025
    elif own_score > opp_score:
        strength -= 0.010

    own_red = _red_count(record, side)
    opp_red = _red_count(record, "away" if side == "home" else "home")
    red_diff = own_red - opp_red
    if red_diff > 0:
        strength -= min(0.15, 0.075 * red_diff)
    elif red_diff < 0:
        strength += min(0.08, 0.04 * abs(red_diff))

    if minute > 75:
        strength -= min(0.08, (minute - 75) * 0.008)
    strength = _clamp(strength, 0.40, 0.95)

    min_evidence = int(_f("GOOL_STATE_MIN_SIDE_EVIDENCE", 3))
    pass_min = _f("GOOL_STATE_SIDE_PASS", 0.68)
    borderline_min = _f("GOOL_STATE_SIDE_BORDERLINE", 0.59)

    if finished or minute <= 0:
        state = HARD_NO
        blocks = ["match_not_live"]
    elif minute < 10 or evidence < min_evidence or confidence is None:
        state = NO_DATA
        blocks = ["side_not_enough_live_evidence"]
    elif bool(raw.get("passed")) and strength >= pass_min:
        state = PASS
        blocks = []
    elif strength >= borderline_min or recent or quality:
        state = BORDERLINE
        blocks = list(raw.get("blocks") or [])
    else:
        state = HARD_NO
        blocks = list(raw.get("blocks") or []) or ["side_live_state_weak"]

    return {
        "side": side,
        "state": state,
        "strength": round(strength, 4),
        "pressure_score": pressure,
        "evidence": evidence,
        "recent_threat": recent,
        "quality_threat": quality,
        "prematch": profile,
        "prematch_strength": profile_strength,
        "raw": raw,
        "blocks": blocks,
    }


def _broad_live_strength(model_result: dict[str, Any]) -> tuple[float | None, bool]:
    live = model_result.get("another_goal_live") or {}
    try:
        pressure = float(live.get("combined_pressure"))
    except (TypeError, ValueError):
        pressure = None
    if pressure is None:
        return None, False
    strength = _clamp(0.50 + (pressure - 0.70) * 0.30, 0.44, 0.93)
    return strength, bool(live.get("passed"))


def _blend(parts: list[tuple[float | None, float]], fallback: float = 0.50) -> float:
    usable = [(value, weight) for value, weight in parts if value is not None]
    if not usable:
        return fallback
    total = sum(weight for _, weight in usable)
    return _clamp(sum(float(value) * weight for value, weight in usable) / total)


def _state_from_strength(
    strength: float,
    *,
    pass_ok: bool,
    hard_no: bool,
    no_data: bool = False,
    pass_min: float = 0.70,
    borderline_min: float = 0.61,
) -> str:
    if hard_no:
        return HARD_NO
    if no_data:
        return NO_DATA
    if pass_ok and strength >= pass_min:
        return PASS
    if strength >= borderline_min:
        return BORDERLINE
    return HARD_NO


def _expert(
    *,
    name: str,
    strength: float,
    state: str,
    blocks: list[str] | None = None,
    diagnostics: dict[str, Any] | None = None,
) -> dict[str, Any]:
    out_blocks = list(dict.fromkeys(str(x) for x in (blocks or []) if str(x)))
    if state == HARD_NO and "expert_hard_no" not in out_blocks:
        out_blocks.insert(0, "expert_hard_no")
    elif state == BORDERLINE and "expert_borderline" not in out_blocks:
        out_blocks.insert(0, "expert_borderline")
    elif state == NO_DATA and "expert_no_data" not in out_blocks:
        out_blocks.insert(0, "expert_no_data")
    return {
        "probability": round(_clamp(strength), 4),
        "metric": "confidence",
        "source": f"goal_state_engine:{name}",
        "passed": state == PASS,
        "state": state,
        "blocks": out_blocks,
        "diagnostics": diagnostics or {},
    }


def build_goal_state_experts(
    record: dict[str, Any],
    *,
    model_result: dict[str, Any] | None = None,
    two_more_analysis: dict[str, Any] | None = None,
    data_quality: float = 1.0,
) -> dict[str, dict[str, Any]]:
    """Build one coherent GOOL view of the match.

    The engine treats LIVE football as the primary signal. Trained models are
    weak priors only. Every public market is derived from the same home/away
    scoring state, so BTTS, first-half goal, team goals and total goals cannot
    independently contradict each other without an explicit state reason.
    """
    model_result = model_result or {}
    two_more_analysis = two_more_analysis or {}
    match = record.get("match") or {}
    minute = int(match.get("minute") or 0)
    hs = int(match.get("home_score") or 0)
    aws = int(match.get("away_score") or 0)
    halftime = bool(match.get("is_halftime"))
    finished = bool(match.get("is_finished"))

    home = _side_state(record, "home")
    away = _side_state(record, "away")
    h = float(home["strength"])
    a = float(away["strength"])
    stronger, weaker = max(h, a), min(h, a)

    broad_live, broad_live_passed = _broad_live_strength(model_result)
    another_prior = _model_prior(model_result, "another_goal")
    side_union = _clamp(stronger + 0.20 * (weaker - 0.50), 0.40, 0.95)
    any_strength = _blend([
        (side_union, 0.60),
        (broad_live, 0.25),
        (another_prior, 0.15),
    ])
    if home["recent_threat"] and away["recent_threat"]:
        any_strength += 0.025
    if minute > 75:
        any_strength -= min(0.08, (minute - 75) * 0.008)
    any_strength = _clamp(any_strength, 0.40, 0.96)
    any_state = _state_from_strength(
        any_strength,
        pass_ok=(
            (home["state"] == PASS or away["state"] == PASS)
            and (broad_live_passed or home["recent_threat"] or away["recent_threat"])
        ),
        hard_no=home["state"] == HARD_NO and away["state"] == HARD_NO,
        no_data=home["state"] == NO_DATA and away["state"] == NO_DATA,
        pass_min=_f("GOOL_STATE_ANY_PASS", 0.70),
        borderline_min=_f("GOOL_STATE_ANY_BORDERLINE", 0.61),
    )

    out: dict[str, dict[str, Any]] = {}
    shared_diag = {
        "home": home,
        "away": away,
        "model_prior_another_goal": another_prior,
        "broad_live_strength": broad_live,
        "broad_live_passed": broad_live_passed,
        "data_quality": data_quality,
    }
    out["another_goal"] = _expert(
        name="another_goal",
        strength=any_strength,
        state=any_state,
        diagnostics=shared_diag,
    )
    out["home_goal"] = _expert(
        name="home_goal",
        strength=h,
        state=home["state"],
        blocks=home["blocks"],
        diagnostics={"side": home, "data_quality": data_quality},
    )
    out["away_goal"] = _expert(
        name="away_goal",
        strength=a,
        state=away["state"],
        blocks=away["blocks"],
        diagnostics={"side": away, "data_quality": data_quality},
    )

    if hs > 0 and aws > 0:
        btts_state = HARD_NO
        btts_strength = 1.0
        btts_blocks = ["btts_already_won"]
        target_side = None
    elif hs == 0 and aws == 0:
        btts_strength = _clamp(min(h, a) + (0.025 if home["state"] == PASS and away["state"] == PASS else 0.0))
        btts_state = _state_from_strength(
            btts_strength,
            pass_ok=home["state"] == PASS and away["state"] == PASS,
            hard_no=home["state"] == HARD_NO or away["state"] == HARD_NO,
            no_data=home["state"] == NO_DATA or away["state"] == NO_DATA,
            pass_min=_f("GOOL_STATE_BTTS_PASS", 0.70),
            borderline_min=_f("GOOL_STATE_BTTS_BORDERLINE", 0.62),
        )
        btts_blocks = []
        target_side = None
    else:
        target_side = "home" if hs == 0 else "away"
        target = home if target_side == "home" else away
        btts_strength = float(target["strength"])
        btts_state = target["state"]
        btts_blocks = list(target["blocks"])
    if minute > 70 and btts_state != HARD_NO:
        btts_strength = _clamp(btts_strength - min(0.06, (minute - 70) * 0.012))
        if btts_state == PASS and btts_strength < _f("GOOL_STATE_BTTS_PASS", 0.70):
            btts_state = BORDERLINE
    out["btts"] = _expert(
        name="btts",
        strength=btts_strength,
        state=btts_state,
        blocks=btts_blocks,
        diagnostics={"home": home, "away": away, "target_side": target_side, "score": [hs, aws]},
    )

    first_half_prior = _model_prior(model_result, "goal_before_ht")
    if finished or halftime or minute >= 45:
        fh_state = HARD_NO
        fh_strength = 0.0
        fh_blocks = ["first_half_closed"]
    elif minute < 10:
        fh_state = NO_DATA
        fh_strength = _blend([(any_strength, 0.65), (first_half_prior, 0.35)])
        fh_blocks = ["first_half_warmup"]
    else:
        fh_strength = _blend([(any_strength, 0.65), (first_half_prior, 0.35)])
        if minute > 25:
            late_penalty = (minute - 25) * 0.006 if minute <= 35 else 0.06 + (minute - 35) * 0.010
            fh_strength -= min(0.16, late_penalty)
        if home["recent_threat"] or away["recent_threat"]:
            fh_strength += 0.025
        fh_strength = _clamp(fh_strength, 0.35, 0.95)
        fh_state = _state_from_strength(
            fh_strength,
            pass_ok=(
                any_state in {PASS, BORDERLINE}
                and (home["recent_threat"] or away["recent_threat"])
            ),
            hard_no=(
                minute >= 32
                and not home["recent_threat"]
                and not away["recent_threat"]
                and fh_strength < _f("GOOL_STATE_FIRST_HALF_BORDERLINE", 0.61)
            ),
            no_data=home["state"] == NO_DATA and away["state"] == NO_DATA,
            pass_min=_f("GOOL_STATE_FIRST_HALF_PASS", 0.70),
            borderline_min=_f("GOOL_STATE_FIRST_HALF_BORDERLINE", 0.61),
        )
        fh_blocks = []
    out["goal_before_ht"] = _expert(
        name="goal_before_ht",
        strength=fh_strength,
        state=fh_state,
        blocks=fh_blocks,
        diagnostics={
            "any_goal_strength": any_strength,
            "model_prior": first_half_prior,
            "minute": minute,
            "recent_threat": bool(home["recent_threat"] or away["recent_threat"]),
        },
    )

    legacy_two = _prob(two_more_analysis.get("confidence_score"))
    time_support = _clamp((65.0 - minute) / 55.0, 0.0, 1.0)
    two_strength = _blend([
        (any_strength, 0.48),
        (stronger, 0.18),
        (weaker, 0.12),
        (legacy_two, 0.14),
        (0.48 + 0.44 * time_support, 0.08),
    ])
    if minute > 45:
        two_strength -= min(0.09, (minute - 45) * 0.006)
    two_strength = _clamp(two_strength, 0.35, 0.94)
    if minute > 60 or finished:
        two_state = HARD_NO
        two_blocks = ["two_goal_window_closed"]
    else:
        legacy_pass = bool(two_more_analysis.get("passed"))
        two_state = _state_from_strength(
            two_strength,
            pass_ok=(
                any_state == PASS
                and (legacy_pass or (home["recent_threat"] and away["recent_threat"]))
                and minute <= 55
            ),
            hard_no=any_state == HARD_NO,
            no_data=any_state == NO_DATA,
            pass_min=_f("GOOL_STATE_TWO_GOALS_PASS", 0.74),
            borderline_min=_f("GOOL_STATE_TWO_GOALS_BORDERLINE", 0.64),
        )
        two_blocks = []
    out["two_more_goals"] = _expert(
        name="two_more_goals",
        strength=two_strength,
        state=two_state,
        blocks=two_blocks,
        diagnostics={
            "any_goal_strength": any_strength,
            "legacy_two_more": two_more_analysis,
            "time_support": time_support,
            "minute": minute,
        },
    )

    return out
