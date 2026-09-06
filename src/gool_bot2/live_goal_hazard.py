from __future__ import annotations

import math
import os
from typing import Any

from .match_context import provider_pair, xg_or_proxy_pair


_INSTALLED = False
_ORIGINAL_BUILD = None


def _f(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        return float(default)


def _pair_total(record: dict[str, Any], key: str) -> float | None:
    try:
        home, away = provider_pair(record, key)
    except Exception:
        return None
    if home is None or away is None:
        return None
    return max(0.0, float(home)) + max(0.0, float(away))


def _momentum_total(record: dict[str, Any], alias: str, window: int) -> float | None:
    momentum = record.get("live_momentum") or {}
    direct = momentum.get(f"{alias}_total_last_{window}m")
    try:
        if direct is not None:
            return max(0.0, float(direct))
    except (TypeError, ValueError):
        pass

    values: list[float] = []
    for side in ("home", "away"):
        raw = momentum.get(f"{side}_{alias}_last_{window}m")
        try:
            if raw is not None:
                values.append(max(0.0, float(raw)))
        except (TypeError, ValueError):
            continue
    return sum(values) if len(values) == 2 else None


def _recent_xg_equivalent(record: dict[str, Any], window: int) -> tuple[float | None, str, int]:
    actual = _momentum_total(record, "xg", window)
    if actual is not None:
        return actual, "provider_xg_delta", 1

    specs = (
        ("shots", 0.025),
        ("sot", 0.070),
        ("big", 0.180),
        ("danger", 0.0035),
    )
    total = 0.0
    evidence = 0
    for alias, weight in specs:
        value = _momentum_total(record, alias, window)
        if value is None:
            continue
        evidence += 1
        total += value * weight
    if evidence == 0:
        return None, "unavailable", 0
    return max(0.0, total), "attack_proxy_delta", evidence


def _weighted_rate(parts: list[tuple[float | None, float]]) -> float | None:
    usable = [(float(value), float(weight)) for value, weight in parts if value is not None]
    if not usable:
        return None
    weight = sum(w for _, w in usable)
    if weight <= 0:
        return None
    return sum(value * w for value, w in usable) / weight


def estimate_live_goal_hazard(record: dict[str, Any], *, period: str) -> dict[str, Any]:
    """Estimate the chance of at least one goal before the active horizon.

    This is deliberately football-only. It uses cumulative xG (or the existing
    conservative attack proxy), real 5m/10m deltas, remaining time and current
    score state. 1xBet and Matchbook are not inputs.

    The returned probability is an engineering live-hazard estimate, not a
    bookmaker-calibrated probability. Its main production role is to veto weak
    pressure-only PASS states, especially late in the match.
    """
    match = record.get("match") or {}
    minute = int(match.get("minute") or 0)
    hs = int(match.get("home_score") or 0)
    aws = int(match.get("away_score") or 0)
    margin = abs(hs - aws)

    if period == "1H":
        horizon = int(_f("GOOL_HAZARD_FIRST_HALF_HORIZON", 46.0))
        min_probability = _f("GOOL_HAZARD_MIN_PROB_1H", 0.40)
        if minute >= 30:
            min_probability = max(min_probability, _f("GOOL_HAZARD_MIN_PROB_1H_LATE", 0.42))
    else:
        horizon = int(_f("GOOL_HAZARD_FULL_TIME_HORIZON", 93.0))
        min_probability = _f("GOOL_HAZARD_MIN_PROB_FT", 0.48)
        if minute >= 70:
            min_probability = max(min_probability, _f("GOOL_HAZARD_MIN_PROB_FT_LATE", 0.50))

    minutes_left = max(0, horizon - minute)
    try:
        xh, xa, xg_source, xg_evidence = xg_or_proxy_pair(record)
    except Exception:
        xh = xa = None
        xg_source = "unavailable"
        xg_evidence = 0
    xg_total = None if xh is None or xa is None else max(0.0, float(xh)) + max(0.0, float(xa))

    recent5, recent5_source, recent5_evidence = _recent_xg_equivalent(record, 5)
    recent10, recent10_source, recent10_evidence = _recent_xg_equivalent(record, 10)

    base_rate = None
    if xg_total is not None and minute > 0:
        # The 12-minute denominator avoids huge cold-start rates from one early
        # shot while leaving normal 15'+ matches unchanged.
        base_rate = xg_total / max(12.0, float(minute))
    recent10_rate = None if recent10 is None else recent10 / 10.0
    recent5_rate = None if recent5 is None else recent5 / 5.0

    rate = _weighted_rate([
        (base_rate, 0.55),
        (recent10_rate, 0.30),
        (recent5_rate, 0.15),
    ])

    blocks: list[str] = []
    if rate is None or xg_total is None:
        return {
            "available": False,
            "passed": False,
            "period": period,
            "minute": minute,
            "minutes_left": minutes_left,
            "probability": None,
            "minimum_probability": min_probability,
            "xg_total": xg_total,
            "xg_source": xg_source,
            "xg_evidence": xg_evidence,
            "recent5_xg_equiv": recent5,
            "recent10_xg_equiv": recent10,
            "blocks": ["live_hazard_no_xg_or_proxy"],
        }

    # Attack-proxy xG is intentionally a little more conservative than provider
    # xG. The rate cap prevents a single explosive five-minute burst from turning
    # into an impossible 90-minute scoring pace.
    source_factor = 1.0 if xg_source == "provider_xg" else 0.92
    rate = min(_f("GOOL_HAZARD_MAX_RATE_PER_MINUTE", 0.060), max(0.0, rate * source_factor))

    score_factor = 1.0
    if period == "FT":
        if minute >= 65 and margin >= 3:
            score_factor = 0.78
        elif minute >= 70 and margin >= 2:
            score_factor = 0.86
        elif minute >= 60 and margin >= 3:
            score_factor = 0.84
    elif minute >= 30 and margin >= 3:
        score_factor = 0.88

    # Genuine recent high-quality pressure can partially offset a dead-game
    # score-state penalty, but never remove it entirely.
    if score_factor < 1.0 and (
        (recent5 is not None and recent5 >= 0.22)
        or (recent10 is not None and recent10 >= 0.40)
    ):
        score_factor = min(0.92, score_factor + 0.08)

    expected_goals_remaining = max(0.0, rate * float(minutes_left) * score_factor)
    probability = 1.0 - math.exp(-min(3.0, expected_goals_remaining))

    # Late matches need actual chance quality, not just historical shot volume.
    # This is the key protection against states such as 73', xG~1.0, 16 shots,
    # 7 SOT, 0 big chances: plenty of activity, but little evidence that another
    # goal is genuinely imminent.
    late_quality_ok = True
    big_total = _pair_total(record, "big_chances")
    if period == "FT" and minute >= 65:
        late_floor = _f("GOOL_HAZARD_LATE_MIN_XG_EQUIV", 1.20)
        if margin >= 3:
            late_floor += _f("GOOL_HAZARD_LARGE_LEAD_EXTRA_XG", 0.15)
        recent_quality = bool(
            (recent5 is not None and recent5 >= _f("GOOL_HAZARD_LATE_RECENT5_XG_EQUIV", 0.16))
            or (recent10 is not None and recent10 >= _f("GOOL_HAZARD_LATE_RECENT10_XG_EQUIV", 0.30))
        )
        cumulative_quality = bool(
            xg_total >= late_floor
            or (
                big_total is not None
                and big_total >= 1.0
                and xg_total >= max(0.85, late_floor - 0.30)
            )
        )
        late_quality_ok = cumulative_quality or recent_quality
        if not late_quality_ok:
            blocks.append("live_hazard_late_quality_low")

    if period == "1H" and minute >= 30:
        fh_quality = bool(
            xg_total >= _f("GOOL_HAZARD_1H_LATE_MIN_XG_EQUIV", 1.05)
            or (recent5 is not None and recent5 >= 0.15)
            or (recent10 is not None and recent10 >= 0.28)
        )
        if not fh_quality:
            late_quality_ok = False
            blocks.append("live_hazard_1h_late_quality_low")

    if minutes_left <= 0:
        blocks.append("live_hazard_window_closed")
    if probability < min_probability:
        blocks.append("live_hazard_probability_low")

    passed = bool(minutes_left > 0 and late_quality_ok and probability >= min_probability)
    return {
        "available": True,
        "passed": passed,
        "period": period,
        "minute": minute,
        "minutes_left": minutes_left,
        "probability": round(probability, 4),
        "minimum_probability": round(min_probability, 4),
        "expected_goals_remaining": round(expected_goals_remaining, 4),
        "rate_per_minute": round(rate, 5),
        "score_factor": round(score_factor, 3),
        "score_margin": margin,
        "xg_total": round(xg_total, 4),
        "xg_source": xg_source,
        "xg_evidence": xg_evidence,
        "recent5_xg_equiv": None if recent5 is None else round(recent5, 4),
        "recent10_xg_equiv": None if recent10 is None else round(recent10, 4),
        "recent5_source": recent5_source,
        "recent10_source": recent10_source,
        "recent5_evidence": recent5_evidence,
        "recent10_evidence": recent10_evidence,
        "big_chances_total": big_total,
        "late_quality_ok": late_quality_ok,
        "blocks": blocks,
    }


def _apply_to_expert(expert: dict[str, Any], hazard: dict[str, Any]) -> None:
    diagnostics = dict(expert.get("diagnostics") or {})
    diagnostics["live_goal_hazard"] = hazard
    expert["diagnostics"] = diagnostics

    probability = hazard.get("probability")
    if probability is not None:
        # The old Goal State number was a pressure/confidence score. For the two
        # public GOOL systems expose the time-aware next-goal hazard instead so a
        # displayed 65% is no longer just a normalized pressure number.
        expert["probability"] = float(probability)
        expert["metric"] = "live_goal_hazard"

    state = str(expert.get("state") or "").upper()
    if state != "PASS" or bool(hazard.get("passed")):
        return

    expert["passed"] = False
    expert["state"] = "BORDERLINE" if hazard.get("available") else "NO_DATA"
    blocks = list(expert.get("blocks") or [])
    blocks.extend(str(item) for item in (hazard.get("blocks") or []) if str(item))
    if "live_goal_hazard_veto" not in blocks:
        blocks.append("live_goal_hazard_veto")
    expert["blocks"] = list(dict.fromkeys(blocks))


def build_goal_state_experts_with_hazard(
    record: dict[str, Any],
    *,
    model_result: dict[str, Any] | None = None,
    two_more_analysis: dict[str, Any] | None = None,
    data_quality: float = 1.0,
) -> dict[str, dict[str, Any]]:
    if _ORIGINAL_BUILD is None:
        raise RuntimeError("live_goal_hazard_not_installed")

    experts = _ORIGINAL_BUILD(
        record,
        model_result=model_result,
        two_more_analysis=two_more_analysis,
        data_quality=data_quality,
    )
    match = record.get("match") or {}
    minute = int(match.get("minute") or 0)

    # Only the two ordinary public systems are changed. Autonomous 1xBet STEAM
    # and Matchbook MONEY FLOW remain independent, exactly as designed.
    if 1 <= minute <= 35 and isinstance(experts.get("goal_before_ht"), dict):
        _apply_to_expert(experts["goal_before_ht"], estimate_live_goal_hazard(record, period="1H"))
    if 46 <= minute <= 75 and isinstance(experts.get("another_goal"), dict):
        _apply_to_expert(experts["another_goal"], estimate_live_goal_hazard(record, period="FT"))
    return experts


def install_live_goal_hazard() -> None:
    global _INSTALLED, _ORIGINAL_BUILD
    if _INSTALLED:
        return

    from . import goal_state_engine as engine

    _ORIGINAL_BUILD = engine.build_goal_state_experts
    engine.build_goal_state_experts = build_goal_state_experts_with_hazard
    _INSTALLED = True
    print(
        "GOOL_LIVE_HAZARD_CONFIG "
        f"ft_min={_f('GOOL_HAZARD_MIN_PROB_FT', 0.48):.2f} "
        f"ft_late={_f('GOOL_HAZARD_MIN_PROB_FT_LATE', 0.50):.2f} "
        f"fh_min={_f('GOOL_HAZARD_MIN_PROB_1H', 0.40):.2f} "
        f"fh_late={_f('GOOL_HAZARD_MIN_PROB_1H_LATE', 0.42):.2f} "
        f"late_xg={_f('GOOL_HAZARD_LATE_MIN_XG_EQUIV', 1.20):.2f}",
        flush=True,
    )


__all__ = [
    "estimate_live_goal_hazard",
    "build_goal_state_experts_with_hazard",
    "install_live_goal_hazard",
]
