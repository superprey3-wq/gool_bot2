from __future__ import annotations

import os
import threading
import time
from typing import Any


_LOCK = threading.RLock()
_REGISTRY: dict[str, dict[str, Any]] = {}
_DYNAMIC_BLOCKS = {"brain_v3_probability_below_bet", "brain_v3_wait_confirmation"}


def _number(value: Any) -> float | None:
    try:
        if value in (None, "", "-"):
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _clamp(value: float, lo: float = 0.0, hi: float = 1.0) -> float:
    return max(lo, min(hi, float(value)))


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        return float(default)


def _under_countercase(decision: dict[str, Any]) -> dict[str, Any]:
    """Build an independent no-more-goal argument from LIVE evidence.

    This is deliberately not ``1 - P(goal)``.  The point is to make Brain V3
    argue against itself: is the apparent OVER signal only a short burst, while
    the wider match is calm/falling?  Historical/browser UNDER trends may add a
    little context, but they can never create the veto without LIVE reasons.
    """
    recent = dict(decision.get("recent") or {})
    xg5 = _number(recent.get("xg5"))
    xg10 = _number(recent.get("xg10"))
    sot5 = _number(recent.get("sot5"))
    shots5 = _number(recent.get("shots5"))
    big5 = _number(recent.get("big5"))
    pressure = _number(decision.get("pressure_index"))
    state = str(decision.get("match_state") or "NO_DATA")
    home_trend = str(decision.get("home_trend") or "WARMING")
    away_trend = str(decision.get("away_trend") or "WARMING")
    minute = int(_number(decision.get("minute")) or 0)
    period = str(decision.get("period") or "")

    score = 0.18
    reasons: list[str] = []
    live_reasons: list[str] = []

    if xg5 is not None:
        if xg5 <= 0.10:
            score += 0.22
            live_reasons.append("xg5_very_low")
        elif xg5 <= 0.16:
            score += 0.14
            live_reasons.append("xg5_low")
        elif xg5 >= 0.28:
            score -= 0.18
            reasons.append("xg5_strong_against_under")

    if xg10 is not None:
        if xg10 <= 0.22:
            score += 0.18
            live_reasons.append("xg10_very_low")
        elif xg10 <= 0.30:
            score += 0.10
            live_reasons.append("xg10_low")
        elif xg10 >= 0.45:
            score -= 0.16
            reasons.append("xg10_strong_against_under")

    if sot5 is not None:
        if sot5 <= 0.0:
            score += 0.14
            live_reasons.append("no_sot_last5")
        elif sot5 <= 1.0:
            score += 0.07
            live_reasons.append("thin_sot_last5")
        elif sot5 >= 2.0:
            score -= 0.10
            reasons.append("sot5_strong_against_under")

    if shots5 is not None:
        if shots5 <= 2.0:
            score += 0.08
            live_reasons.append("few_shots_last5")
        elif shots5 >= 5.0:
            score -= 0.08
            reasons.append("shots5_strong_against_under")

    if big5 is not None and big5 <= 0.0:
        score += 0.04
        live_reasons.append("no_big_chance_last5")

    if pressure is not None:
        if pressure < 0.50:
            score += 0.14
            live_reasons.append("pressure_low")
        elif pressure < 0.62:
            score += 0.07
            live_reasons.append("pressure_moderate")
        elif pressure >= 0.75:
            score -= 0.12
            reasons.append("pressure_high_against_under")

    if state == "CALM":
        score += 0.18
        live_reasons.append("match_calm")
    elif state in {"NO_DATA", "WARMING"}:
        score += 0.08
        live_reasons.append("match_not_mature")
    elif state in {"HOME_BUILDING", "AWAY_BUILDING"}:
        score += 0.04
        live_reasons.append("pressure_only_building")
    elif state in {"HOME_SIEGE", "AWAY_SIEGE", "END_TO_END"}:
        score -= 0.18
        reasons.append("strong_state_against_under")

    if home_trend == "FALLING" and away_trend == "FALLING":
        score += 0.12
        live_reasons.append("both_trends_falling")
    elif "RISING" in {home_trend, away_trend}:
        score -= 0.08
        reasons.append("rising_trend_against_under")
    elif home_trend == "STEADY" and away_trend == "STEADY":
        score += 0.05
        live_reasons.append("both_trends_steady")

    # Short one-window spike with no 10m depth is a classic false OVER pattern.
    if (
        xg5 is not None
        and xg5 >= 0.25
        and (xg10 is None or xg10 < max(0.34, xg5 * 1.25))
        and (sot5 is None or sot5 <= 1.0)
    ):
        score += 0.18
        live_reasons.append("short_burst_without_depth")

    if period == "1H" and minute >= 30:
        score += 0.05
        live_reasons.append("late_first_half_clock")
    elif period == "2H" and minute >= 68:
        score += 0.07
        live_reasons.append("late_second_half_clock")

    browser = dict(decision.get("browser365") or {})
    under_rate = _number(browser.get("under_rate"))
    over_rate = _number(browser.get("over_rate"))
    context_reasons: list[str] = []
    if under_rate is not None and under_rate >= 0.70:
        score += 0.06
        context_reasons.append("365_under_trend")
    if over_rate is not None and over_rate >= 0.70:
        score -= 0.06
        context_reasons.append("365_over_trend")

    score = _clamp(score)
    threshold = _clamp(_env_float("GOOL_BRAIN_V3_UNDER_VETO_MIN", 0.64), 0.50, 0.90)
    minimum_live = max(2, int(_env_float("GOOL_BRAIN_V3_UNDER_VETO_MIN_LIVE_REASONS", 3)))
    strong = bool(score >= threshold and len(live_reasons) >= minimum_live)
    return {
        "version": 1,
        "no_more_goal_risk": round(score, 4),
        "threshold": round(threshold, 4),
        "strong": strong,
        "live_reason_count": len(live_reasons),
        "live_reasons": live_reasons,
        "context_reasons": context_reasons,
        "counterevidence": reasons,
        "under_rate_365": None if under_rate is None else round(under_rate, 4),
        "over_rate_365": None if over_rate is None else round(over_rate, 4),
    }


def _selection_score(decision: dict[str, Any], countercase: dict[str, Any]) -> tuple[float, list[str]]:
    probability = _number(decision.get("probability")) or 0.0
    live_probability = _number(decision.get("live_probability")) or probability
    quality = _number(decision.get("data_quality")) or 0.0
    pressure = _number(decision.get("pressure_index")) or 0.0
    recent = dict(decision.get("recent") or {})
    xg5 = _number(recent.get("xg5")) or 0.0
    sot5 = _number(recent.get("sot5")) or 0.0
    big5 = _number(recent.get("big5")) or 0.0
    state = str(decision.get("match_state") or "NO_DATA")
    under_risk = _number(countercase.get("no_more_goal_risk")) or 0.0

    recent_strength = _clamp(0.60 * (xg5 / 0.45) + 0.25 * (sot5 / 3.0) + 0.15 * big5)
    if state in {"HOME_SIEGE", "AWAY_SIEGE", "END_TO_END"}:
        state_strength = 1.0
    elif state in {"HOME_PRESSURE", "AWAY_PRESSURE"}:
        state_strength = 0.85
    elif state in {"HOME_BUILDING", "AWAY_BUILDING"}:
        state_strength = 0.65
    else:
        state_strength = 0.30

    score = (
        0.45 * _clamp(probability)
        + 0.20 * _clamp(live_probability)
        + 0.12 * _clamp(quality)
        + 0.10 * _clamp(pressure)
        + 0.08 * recent_strength
        + 0.05 * state_strength
        - 0.15 * _clamp(under_risk)
    )
    score = _clamp(score)
    reasons = [
        f"p={probability:.3f}",
        f"live={live_probability:.3f}",
        f"quality={quality:.2f}",
        f"pressure={pressure:.2f}",
        f"xg5={xg5:.2f}",
        f"under_risk={under_risk:.2f}",
        f"state={state}",
    ]
    return score, reasons


def _sync_expert(experts: dict[str, Any], decision: dict[str, Any]) -> None:
    strategy = str(decision.get("strategy") or "")
    expert = experts.get(strategy) if strategy else None
    if not isinstance(expert, dict) or not str(expert.get("source") or "").startswith("brain_v3:"):
        return
    status = str(decision.get("status") or "WATCH")
    expert["probability"] = round(float(_number(decision.get("probability")) or 0.50), 4)
    expert["passed"] = status == "BET"
    expert["state"] = "PASS" if status == "BET" else "BORDERLINE"
    expert["blocks"] = list(decision.get("blocks") or [])
    diagnostics = dict(expert.get("diagnostics") or {})
    diagnostics["brain_v3"] = decision
    expert["diagnostics"] = diagnostics


def _prune(now: float, ttl: float) -> None:
    stale = [key for key, row in _REGISTRY.items() if now - float(row.get("seen_at") or 0.0) > ttl]
    for key in stale:
        _REGISTRY.pop(key, None)


def audit_brain_v3_decision(
    record: dict[str, Any],
    experts: dict[str, Any],
    decision: dict[str, Any],
) -> dict[str, Any]:
    """Final ordinary-BET audit: argue UNDER, then ask why this match vs another."""
    if not isinstance(decision, dict) or not bool(decision.get("active")):
        return decision

    match = dict(record.get("match") or {})
    match_id = str(match.get("flashscore_event_id") or "").strip()
    if not match_id:
        return decision

    pre_status = str(decision.get("status") or "WATCH")
    countercase = _under_countercase(decision)
    decision["countercase"] = countercase

    blocks = [str(row) for row in list(decision.get("blocks") or []) if str(row) not in _DYNAMIC_BLOCKS]
    verdict = "KEEP"
    if pre_status == "BET" and bool(countercase.get("strong")):
        decision["status"] = "READY"
        blocks.append("brain_v3_under_countercase_strong")
        verdict = "BLOCK_UNDER"

    score, score_reasons = _selection_score(decision, countercase)
    period = str(decision.get("period") or "")
    strategy = str(decision.get("strategy") or "")
    now = time.time()
    ttl = max(45.0, _env_float("GOOL_BRAIN_V3_SELECTION_TTL_SECONDS", 120.0))
    gap_min = max(0.02, min(0.15, _env_float("GOOL_BRAIN_V3_SELECTION_BETTER_GAP", 0.055)))
    eligible_rival = bool(
        pre_status == "BET"
        and not bool(countercase.get("strong"))
        and bool(decision.get("live_foundation"))
        and bool(decision.get("sustained_pressure"))
    )

    better: dict[str, Any] | None = None
    with _LOCK:
        _prune(now, ttl)
        # Register all genuine BET-quality candidates, even if the final audit
        # blocks this one, so the rolling field is populated across live matches.
        _REGISTRY[match_id] = {
            "match_id": match_id,
            "home": match.get("home"),
            "away": match.get("away"),
            "minute": int(_number(decision.get("minute")) or 0),
            "period": period,
            "strategy": strategy,
            "score": round(score, 4),
            "probability": round(float(_number(decision.get("probability")) or 0.0), 4),
            "pre_status": pre_status,
            "eligible_rival": eligible_rival,
            "under_risk": countercase.get("no_more_goal_risk"),
            "seen_at": now,
        }
        rivals = [
            row for key, row in _REGISTRY.items()
            if key != match_id
            and bool(row.get("eligible_rival"))
            and str(row.get("strategy") or "") == strategy
        ]
        if rivals:
            candidate = max(rivals, key=lambda row: float(row.get("score") or 0.0))
            if float(candidate.get("score") or 0.0) >= score + gap_min:
                better = dict(candidate)

    if str(decision.get("status") or "") == "BET" and better is not None:
        decision["status"] = "READY"
        blocks.append("brain_v3_better_candidate_available")
        verdict = "BLOCK_BETTER"

    if str(decision.get("status") or "") != "BET":
        if (_number(decision.get("probability")) or 0.0) < (_number(decision.get("bet_min")) or 0.70):
            blocks.append("brain_v3_probability_below_bet")
        elif not any(
            block in blocks
            for block in ("brain_v3_under_countercase_strong", "brain_v3_better_candidate_available")
        ):
            blocks.append("brain_v3_wait_confirmation")

    selection = {
        "version": 1,
        "score": round(score, 4),
        "score_reasons": score_reasons,
        "strategy": strategy,
        "registry_ttl_seconds": round(ttl, 1),
        "better_gap_required": round(gap_min, 4),
        "better_candidate": better,
        "verdict": verdict,
        "why_this_match": (
            "strongest_recent_candidate_known" if better is None else "stronger_recent_candidate_exists"
        ),
    }
    decision["selection_audit"] = selection
    decision["blocks"] = list(dict.fromkeys(blocks))
    thoughts = list(decision.get("thoughts") or [])
    thoughts.append(f"under_risk={float(countercase.get('no_more_goal_risk') or 0):.2f}")
    thoughts.append(f"selection={score:.2f}/{verdict}")
    decision["thoughts"] = thoughts
    record["brain_v3_decision"] = decision
    _sync_expert(experts, decision)

    print(
        f"GOOL_BRAIN_V3_AUDIT match={match_id} minute={int(_number(decision.get('minute')) or 0)} "
        f"stage={pre_status}->{decision.get('status')} selection={score:.3f} "
        f"under={float(countercase.get('no_more_goal_risk') or 0):.3f} "
        f"verdict={verdict} better={(better or {}).get('match_id') or '-'} "
        f"why={','.join(list(countercase.get('live_reasons') or [])[:4]) or '-'}",
        flush=True,
    )
    return decision


def _reset_registry_for_tests() -> None:
    with _LOCK:
        _REGISTRY.clear()


__all__ = ["audit_brain_v3_decision", "_under_countercase", "_selection_score"]
