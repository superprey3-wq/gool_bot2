from __future__ import annotations

import math
import os
from typing import Any

from .match_context import xg_or_proxy_pair


WATCH = "WATCH"
READY = "READY"
BET = "BET"


def _f(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        return float(default)


def _number(value: Any) -> float | None:
    try:
        if value is None:
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _clamp(value: float, lo: float = 0.0, hi: float = 1.0) -> float:
    return max(lo, min(hi, float(value)))


def _window_total(window: dict[str, Any] | None, alias: str) -> float | None:
    if not isinstance(window, dict):
        return None
    home = _number(window.get(f"home_{alias}"))
    away = _number(window.get(f"away_{alias}"))
    if home is None or away is None:
        return None
    return max(0.0, home) + max(0.0, away)


def _window_xg_equiv(window: dict[str, Any] | None) -> tuple[float | None, str]:
    if not isinstance(window, dict):
        return None, "unavailable"
    actual = _window_total(window, "xg")
    if actual is not None:
        return actual, "provider_xg"

    specs = (
        ("shots", 0.025),
        ("sot", 0.070),
        ("big", 0.180),
        ("danger", 0.0035),
    )
    total = 0.0
    evidence = 0
    for alias, weight in specs:
        value = _window_total(window, alias)
        if value is None:
            continue
        evidence += 1
        total += value * weight
    if evidence < 2:
        return None, "unavailable"
    return max(0.0, total), "attack_proxy"


def _weighted_rate(parts: list[tuple[float | None, float]]) -> float | None:
    usable = [(float(value), float(weight)) for value, weight in parts if value is not None]
    if not usable:
        return None
    total_weight = sum(weight for _, weight in usable)
    if total_weight <= 0:
        return None
    return sum(value * weight for value, weight in usable) / total_weight


def _prematch_support(record: dict[str, Any], profile: dict[str, Any] | None) -> dict[str, Any]:
    payload = profile if isinstance(profile, dict) else (record.get("prematch_goal_profile") or {})
    active = dict((payload or {}).get("active") or {})
    if not bool(active.get("available")):
        return {
            "available": False,
            "probability": None,
            "sample": 0,
            "adjustment_pp": 0.0,
            "label": "unknown",
        }

    probability = _number(active.get("one_more_probability"))
    if probability is None:
        return {
            "available": False,
            "probability": None,
            "sample": 0,
            "adjustment_pp": 0.0,
            "label": "unknown",
        }

    pair_sample = max(0, int(_number(active.get("pair_sample")) or 0))
    h2h_sample = max(0, int(_number(active.get("h2h_sample")) or 0))
    effective_sample = pair_sample + min(5, h2h_sample) * 0.5
    reliability = min(1.0, effective_sample / 8.0)

    # PREMATCH is context, never the engine. Even a very strong history can only
    # move an already-live scenario by four percentage points.
    adjustment = (float(probability) - 0.55) * 0.20 * reliability
    adjustment = max(-0.02, min(0.04, adjustment))
    if probability >= 0.68 and reliability >= 0.50:
        label = "supportive"
    elif probability <= 0.42 and reliability >= 0.50:
        label = "caution"
    else:
        label = "neutral"
    return {
        "available": True,
        "probability": round(_clamp(probability), 4),
        "pair_sample": pair_sample,
        "h2h_sample": h2h_sample,
        "sample": round(effective_sample, 1),
        "reliability": round(reliability, 3),
        "adjustment_pp": round(adjustment * 100.0, 2),
        "label": label,
    }


def _active_strategy(minute: int, halftime: bool) -> tuple[str | None, str | None, int]:
    if halftime:
        return None, None, 0
    if 1 <= minute <= 35:
        return "goal_before_ht", "1H", 47
    # Ordinary Brain V3 entries become increasingly fragile after 70'.  The old
    # inclusive 75' window allowed a fresh BET with only ~15 regulation minutes
    # left (plus an optimistic stoppage horizon). Keep 74'+ for autonomous market
    # systems, but stop ordinary football entries before that point.
    if 46 <= minute <= 73:
        return "another_goal", "2H", 95
    return None, None, 0


def evaluate_brain_v3(
    record: dict[str, Any],
    *,
    data_quality: float,
    prematch_profile: dict[str, Any] | None = None,
) -> dict[str, Any]:
    match = record.get("match") or {}
    minute = int(match.get("minute") or 0)
    halftime = bool(match.get("is_halftime"))
    strategy, period, horizon = _active_strategy(minute, halftime)
    memory = dict(record.get("brain_v3_memory") or {})

    if strategy is None or period is None:
        return {
            "version": 3,
            "status": WATCH,
            "active": False,
            "strategy": strategy,
            "period": period,
            "probability": None,
            "confidence_score": 0.0,
            "blocks": ["brain_v3_outside_entry_window"],
        }

    windows = dict(memory.get("windows") or {})
    pressure = dict(memory.get("pressure") or {})
    epoch = dict(memory.get("score_epoch") or {})
    w5 = windows.get("5m")
    w10 = windows.get("10m")
    w15 = windows.get("15m")

    home_pressure = _number(pressure.get("home"))
    away_pressure = _number(pressure.get("away"))
    state = str(pressure.get("state") or "NO_DATA")
    home_trend = str(((pressure.get("home_trend") or {}).get("state") or "WARMING"))
    away_trend = str(((pressure.get("away_trend") or {}).get("state") or "WARMING"))

    h = max(0.0, home_pressure or 0.0)
    a = max(0.0, away_pressure or 0.0)
    pressure_index = _clamp(max(h, a) + 0.18 * min(h, a))

    xg5, xg5_source = _window_xg_equiv(w5)
    xg10, xg10_source = _window_xg_equiv(w10)
    xg15, xg15_source = _window_xg_equiv(w15)
    shots5 = _window_total(w5, "shots")
    sot5 = _window_total(w5, "sot")
    big5 = _window_total(w5, "big")
    shots10 = _window_total(w10, "shots")
    sot10 = _window_total(w10, "sot")
    big10 = _window_total(w10, "big")

    try:
        xh, xa, cumulative_source, cumulative_evidence = xg_or_proxy_pair(record)
    except Exception:
        xh = xa = None
        cumulative_source = "unavailable"
        cumulative_evidence = 0
    cumulative_xg = None if xh is None or xa is None else max(0.0, float(xh)) + max(0.0, float(xa))

    base_rate = None if cumulative_xg is None or minute <= 0 else cumulative_xg / max(12.0, float(minute))
    rate5 = None if xg5 is None else xg5 / 5.0
    rate10 = None if xg10 is None else xg10 / 10.0
    rate15 = None if xg15 is None else xg15 / 15.0
    live_rate = _weighted_rate([
        (rate5, 0.48),
        (rate10, 0.30),
        (rate15, 0.10),
        (base_rate, 0.12),
    ])

    minutes_left = max(0, horizon - minute)
    hs = int(match.get("home_score") or 0)
    aws = int(match.get("away_score") or 0)
    margin = abs(hs - aws)

    trend_multiplier = 1.0
    if home_trend == "RISING" or away_trend == "RISING":
        trend_multiplier += 0.06
    strongest_side = "home" if h >= a else "away"
    strongest_trend = home_trend if strongest_side == "home" else away_trend
    if strongest_trend == "FALLING" and min(h, a) < 0.38:
        trend_multiplier -= 0.10

    score_multiplier = 1.0
    if period == "2H":
        if margin == 1:
            trailing_pressure = a if hs > aws else h
            if trailing_pressure >= 0.50:
                score_multiplier += 0.08
        elif margin >= 3 and minute >= 65:
            score_multiplier *= 0.82
        elif margin >= 2 and minute >= 70:
            score_multiplier *= 0.90
        # A draw used to receive +5% even at 75'. That is exactly the kind of
        # late generic boost that can turn a merely busy 1:1 into a false BET.
        # Keep the incentive only before the late-entry regime begins.
        if hs == aws and 65 <= minute < 70 and h >= 0.45 and a >= 0.45:
            score_multiplier += 0.05
    elif minute >= 30 and margin >= 2:
        score_multiplier *= 0.92

    if live_rate is None:
        live_probability = None
        expected_remaining = None
    else:
        pressure_multiplier = 0.85 + 0.35 * pressure_index
        effective_rate = max(0.0, min(0.080, live_rate * pressure_multiplier * trend_multiplier * score_multiplier))
        expected_remaining = effective_rate * float(minutes_left)
        live_probability = 1.0 - math.exp(-min(3.2, expected_remaining))

    prematch = _prematch_support(record, prematch_profile)
    adjusted_probability = live_probability
    if adjusted_probability is not None:
        adjusted_probability = _clamp(adjusted_probability + float(prematch.get("adjustment_pp") or 0.0) / 100.0)

    quality = _clamp(data_quality)
    confidence_cap = 0.92
    if quality < 0.45:
        confidence_cap = 0.69
    elif quality < 0.60:
        confidence_cap = 0.76
    elif cumulative_source != "provider_xg" and xg5_source != "provider_xg":
        confidence_cap = 0.84
    if adjusted_probability is not None:
        adjusted_probability = min(adjusted_probability, confidence_cap)

    epoch_age = max(0, int(_number(epoch.get("age_minutes")) or 0))
    epoch_samples = max(0, int(_number(epoch.get("samples")) or 0))
    pressure_ok = state in {
        "HOME_SIEGE",
        "AWAY_SIEGE",
        "END_TO_END",
        "HOME_PRESSURE",
        "AWAY_PRESSURE",
        "HOME_BUILDING",
        "AWAY_BUILDING",
    }

    quality_floor = 0.14 if minute < 70 else 0.18
    recent_quality = bool(
        (xg5 is not None and xg5 >= quality_floor)
        or (sot5 is not None and sot5 >= 2.0)
        or (big5 is not None and big5 >= 1.0)
        or (shots5 is not None and shots5 >= 5.0 and pressure_index >= 0.72)
    )

    if w10:
        sustained = bool(
            (xg10 is not None and xg10 >= 0.28)
            or (sot10 is not None and sot10 >= 3.0)
            or (big10 is not None and big10 >= 1.0)
            or (shots10 is not None and shots10 >= 7.0)
        )
    else:
        sustained = bool(
            epoch_age >= 5
            and (
                (xg5 is not None and xg5 >= 0.28)
                or (sot5 is not None and sot5 >= 3.0)
                or (big5 is not None and big5 >= 1.0)
            )
        )
    if not sustained and (home_trend == "RISING" or away_trend == "RISING") and xg5 is not None and xg5 >= 0.18:
        sustained = True

    # Late draw is a special risk class. 1:1/2:2 after 70' is not allowed to
    # piggy-back on ordinary pressure thresholds; it needs a genuinely violent
    # LIVE phase across both the 5m and 10m windows. PREMATCH cannot satisfy this.
    late_draw = bool(period == "2H" and minute >= 70 and hs == aws)
    late_draw_exceptional = bool(
        not late_draw
        or (
            state in {"END_TO_END", "HOME_SIEGE", "AWAY_SIEGE"}
            and quality >= 0.60
            and xg5 is not None
            and xg5 >= _f("GOOL_BRAIN_V3_LATE_DRAW_XG5_MIN", 0.30)
            and xg10 is not None
            and xg10 >= _f("GOOL_BRAIN_V3_LATE_DRAW_XG10_MIN", 0.50)
            and (
                (sot5 is not None and sot5 >= _f("GOOL_BRAIN_V3_LATE_DRAW_SOT5_MIN", 2.0))
                or (big5 is not None and big5 >= 1.0)
            )
            and strongest_trend != "FALLING"
        )
    )

    bet_min = _f("GOOL_BRAIN_V3_BET_MIN", 0.70)
    if minute >= 70:
        bet_min = max(bet_min, _f("GOOL_BRAIN_V3_LATE_BET_MIN", 0.72))
    if late_draw:
        bet_min = max(bet_min, _f("GOOL_BRAIN_V3_LATE_DRAW_BET_MIN", 0.76))
    if period == "1H" and minute >= 30:
        bet_min = max(bet_min, _f("GOOL_BRAIN_V3_1H_LATE_BET_MIN", 0.71))
    if margin >= 3:
        bet_min = max(bet_min, _f("GOOL_BRAIN_V3_LARGE_LEAD_BET_MIN", 0.75))
    ready_min = min(bet_min, _f("GOOL_BRAIN_V3_READY_MIN", 0.60))

    blocks: list[str] = []
    thoughts: list[str] = [f"state={state}", f"time_left={minutes_left}m"]
    if xg5 is not None:
        thoughts.append(f"xg5={xg5:.2f}")
    if xg10 is not None:
        thoughts.append(f"xg10={xg10:.2f}")
    if sot5 is not None:
        thoughts.append(f"sot5={sot5:.0f}")
    thoughts.append(f"trend={home_trend}/{away_trend}")
    if late_draw:
        thoughts.append(f"late_draw_exceptional={late_draw_exceptional}")
    if prematch.get("available"):
        thoughts.append(
            f"prematch={prematch.get('label')} {float(prematch.get('probability') or 0)*100:.0f}% "
            f"({float(prematch.get('adjustment_pp') or 0):+.1f}pp)"
        )

    if not memory:
        blocks.append("brain_v3_memory_missing")
    if w5 is None:
        blocks.append("brain_v3_wait_5m_history")
    if epoch_age < 3 and sum(epoch.get("score") or [0, 0]) > 0:
        blocks.append("brain_v3_post_goal_observe")
    if epoch_samples < 4:
        blocks.append("brain_v3_not_enough_epoch_snapshots")
    if quality < 0.35:
        blocks.append("brain_v3_data_quality_low")
    if not pressure_ok:
        blocks.append("brain_v3_pressure_not_confirmed")
    if not recent_quality:
        blocks.append("brain_v3_recent_chance_quality_low")
    if not sustained:
        blocks.append("brain_v3_pressure_not_sustained")
    if strongest_trend == "FALLING" and state not in {"END_TO_END", "HOME_SIEGE", "AWAY_SIEGE"}:
        blocks.append("brain_v3_pressure_falling")
    if late_draw and not late_draw_exceptional:
        blocks.append("brain_v3_late_draw_not_exceptional")
    if adjusted_probability is None:
        blocks.append("brain_v3_probability_unavailable")

    live_foundation = bool(
        w5 is not None
        and epoch_age >= 3
        and epoch_samples >= 4
        and quality >= 0.35
        and pressure_ok
        and recent_quality
    )
    bet_ready = bool(
        live_foundation
        and sustained
        and "brain_v3_pressure_falling" not in blocks
        and "brain_v3_late_draw_not_exceptional" not in blocks
    )

    if adjusted_probability is None or not live_foundation:
        status = WATCH
    elif adjusted_probability >= bet_min and bet_ready:
        status = BET
    elif adjusted_probability >= ready_min:
        status = READY
    else:
        status = WATCH

    if status != BET:
        if adjusted_probability is not None and adjusted_probability < bet_min:
            blocks.append("brain_v3_probability_below_bet")
        elif adjusted_probability is not None and not bet_ready:
            blocks.append("brain_v3_wait_confirmation")
    blocks = list(dict.fromkeys(blocks))

    decision = {
        "version": 3,
        "active": True,
        "status": status,
        "strategy": strategy,
        "period": period,
        "minute": minute,
        "score": [hs, aws],
        "minutes_left": minutes_left,
        "match_state": state,
        "pressure_index": round(pressure_index, 4),
        "home_pressure": None if home_pressure is None else round(home_pressure, 4),
        "away_pressure": None if away_pressure is None else round(away_pressure, 4),
        "home_trend": home_trend,
        "away_trend": away_trend,
        "recent": {
            "xg5": None if xg5 is None else round(xg5, 4),
            "xg10": None if xg10 is None else round(xg10, 4),
            "xg15": None if xg15 is None else round(xg15, 4),
            "xg5_source": xg5_source,
            "xg10_source": xg10_source,
            "xg15_source": xg15_source,
            "shots5": shots5,
            "sot5": sot5,
            "big5": big5,
            "shots10": shots10,
            "sot10": sot10,
            "big10": big10,
        },
        "cumulative_xg_equiv": None if cumulative_xg is None else round(cumulative_xg, 4),
        "cumulative_xg_source": cumulative_source,
        "cumulative_xg_evidence": cumulative_evidence,
        "expected_goals_remaining": None if expected_remaining is None else round(expected_remaining, 4),
        "live_probability": None if live_probability is None else round(live_probability, 4),
        "prematch": prematch,
        "probability": None if adjusted_probability is None else round(adjusted_probability, 4),
        "confidence_score": 0.0 if adjusted_probability is None else round(adjusted_probability * 100.0, 1),
        "confidence_cap": round(confidence_cap, 4),
        "bet_min": round(bet_min, 4),
        "ready_min": round(ready_min, 4),
        "data_quality": round(quality, 4),
        "score_epoch": epoch,
        "recent_quality": recent_quality,
        "sustained_pressure": sustained,
        "live_foundation": live_foundation,
        "late_draw": late_draw,
        "late_draw_exceptional": late_draw_exceptional,
        "thoughts": thoughts,
        "blocks": blocks,
    }
    record["brain_v3_decision"] = decision
    return decision


def apply_brain_v3_to_experts(
    record: dict[str, Any],
    experts: dict[str, dict[str, Any]],
    *,
    data_quality: float,
    prematch_profile: dict[str, Any] | None = None,
) -> dict[str, Any]:
    decision = evaluate_brain_v3(
        record,
        data_quality=data_quality,
        prematch_profile=prematch_profile,
    )
    strategy = str(decision.get("strategy") or "")
    if not strategy or strategy not in experts:
        return decision

    legacy = dict(experts.get(strategy) or {})
    legacy_diag = dict(legacy.get("diagnostics") or {})
    probability = _number(decision.get("probability"))
    if probability is None:
        probability = 0.50

    status = str(decision.get("status") or WATCH)
    if status == BET:
        expert_state = "PASS"
    elif not bool(decision.get("live_foundation")) and "brain_v3_probability_unavailable" in (decision.get("blocks") or []):
        expert_state = "NO_DATA"
    else:
        expert_state = "BORDERLINE"

    experts[strategy] = {
        "probability": round(_clamp(probability), 4),
        "metric": "brain_v3_probability",
        "source": "brain_v3:state_machine",
        "passed": status == BET,
        "state": expert_state,
        "blocks": list(decision.get("blocks") or []),
        "diagnostics": {
            "brain_v3": decision,
            "legacy_v2": {
                "probability": legacy.get("probability"),
                "metric": legacy.get("metric"),
                "source": legacy.get("source"),
                "passed": legacy.get("passed"),
                "state": legacy.get("state"),
                "blocks": list(legacy.get("blocks") or []),
            },
            "legacy_diagnostics": legacy_diag,
        },
    }
    return decision


__all__ = [
    "WATCH",
    "READY",
    "BET",
    "evaluate_brain_v3",
    "apply_brain_v3_to_experts",
]
