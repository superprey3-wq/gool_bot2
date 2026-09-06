from __future__ import annotations

import os
from typing import Any, Callable


_INSTALLED = False
_DYNAMIC_BLOCKS = {
    "brain_v3_probability_below_bet",
    "brain_v3_wait_confirmation",
}


def _number(value: Any) -> float | None:
    try:
        if value in (None, "", "-"):
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _clamp(value: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, float(value)))


def _identity(row: dict[str, Any]) -> tuple[Any, ...]:
    event_id = str(row.get("event_id") or "").strip()
    if event_id:
        return ("event", event_id)
    return (
        str(row.get("home") or "").casefold().strip(),
        str(row.get("away") or "").casefold().strip(),
        str(row.get("timestamp") or "")[:10],
        int(_number(row.get("home_score")) or 0),
        int(_number(row.get("away_score")) or 0),
    )


def _dedupe(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    seen: set[tuple[Any, ...]] = set()
    for row in rows:
        if not isinstance(row, dict):
            continue
        key = _identity(row)
        if key in seen:
            continue
        seen.add(key)
        out.append(row)
    return out


def _finished_rows(rows: Any) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for row in _dedupe([dict(r) for r in (rows or []) if isinstance(r, dict)]):
        home = _number(row.get("home_score"))
        away = _number(row.get("away_score"))
        if home is None or away is None:
            continue
        out.append(row)
    return out


def _over_rate(rows: list[dict[str, Any]], line: float) -> float | None:
    if not rows:
        return None
    hits = 0
    used = 0
    for row in rows:
        home = _number(row.get("home_score"))
        away = _number(row.get("away_score"))
        if home is None or away is None:
            continue
        used += 1
        hits += int(home + away > line)
    return None if used <= 0 else hits / used


def _mean(values: list[float | None]) -> float | None:
    usable = [float(v) for v in values if v is not None]
    return None if not usable else sum(usable) / len(usable)


def _effective_rate(overall: list[dict[str, Any]], venue: list[dict[str, Any]], line: float) -> tuple[float | None, int, int]:
    overall_rate = _over_rate(overall, line)
    venue_rate = _over_rate(venue, line)
    overall_n = len(overall)
    venue_n = len(venue)
    if overall_rate is None:
        return venue_rate, overall_n, venue_n
    if venue_rate is None or venue_n < 3:
        return overall_rate, overall_n, venue_n
    venue_weight = min(0.30, 0.15 + 0.03 * venue_n)
    return (1.0 - venue_weight) * overall_rate + venue_weight * venue_rate, overall_n, venue_n


def _source_rows(rows: list[dict[str, Any]], token: str) -> list[dict[str, Any]]:
    token = token.casefold()
    return [row for row in rows if token in str(row.get("source") or "").casefold()]


def score_aware_history_trend(record: dict[str, Any]) -> dict[str, Any]:
    """Turn recent full-match totals into a small score-aware context signal.

    Example: at 1:1 the next full-match total line is O2.5. If both teams'
    recent matches frequently clear O2.5, that is useful context for one more
    goal, but it is never allowed to replace LIVE pressure/chance evidence.
    """
    match = record.get("match") or {}
    minute = int(_number(match.get("minute")) or 0)
    home_score = max(0, int(_number(match.get("home_score")) or 0))
    away_score = max(0, int(_number(match.get("away_score")) or 0))
    total_now = home_score + away_score
    period = "1H" if minute <= 45 else "2H" if minute >= 46 else "PRE"

    if total_now < 2 or total_now > 4:
        return {
            "available": False,
            "score": [home_score, away_score],
            "current_total": total_now,
            "period": period,
            "reason": "score_not_directly_relevant_to_full_total_trend",
            "adjustment_pp": 0.0,
        }

    context = record.get("prematch_context") or {}
    line = float(total_now) + 0.5
    home_rows = _finished_rows(context.get("home_recent"))
    away_rows = _finished_rows(context.get("away_recent"))
    home_venue = _finished_rows(context.get("home_at_home"))
    away_venue = _finished_rows(context.get("away_away"))
    h2h_rows = _finished_rows(context.get("h2h"))

    home_rate, home_n, home_venue_n = _effective_rate(home_rows, home_venue, line)
    away_rate, away_n, away_venue_n = _effective_rate(away_rows, away_venue, line)
    pair_sample = min(home_n, away_n)
    base_rate = _mean([home_rate, away_rate])
    h2h_rate = _over_rate(h2h_rows, line)
    h2h_n = len(h2h_rows)
    if base_rate is not None and h2h_rate is not None and h2h_n >= 2:
        h2h_weight = min(0.15, 0.05 + 0.02 * h2h_n)
        rate = (1.0 - h2h_weight) * base_rate + h2h_weight * h2h_rate
    else:
        h2h_weight = 0.0
        rate = base_rate if base_rate is not None else h2h_rate

    minimum = max(3, int(float(os.getenv("GOOL_BRAIN_V3_TREND_MIN_SAMPLE", "4"))))
    if rate is None or pair_sample < minimum:
        return {
            "available": False,
            "score": [home_score, away_score],
            "current_total": total_now,
            "period": period,
            "next_full_total_line": line,
            "pair_sample": pair_sample,
            "home_sample": home_n,
            "away_sample": away_n,
            "reason": "trend_sample_too_small",
            "adjustment_pp": 0.0,
        }

    reliability = min(1.0, pair_sample / 8.0)
    period_weight = 1.0 if period == "2H" else 0.55
    line_weight = {2: 1.0, 3: 0.90, 4: 0.75}.get(total_now, 0.75)
    raw_adjustment = (float(rate) - 0.55) * 0.10 * reliability * period_weight * line_weight
    min_pp = max(-2.0, min(0.0, float(os.getenv("GOOL_BRAIN_V3_TREND_MIN_ADJUST_PP", "-1.5"))))
    max_pp = min(3.0, max(0.0, float(os.getenv("GOOL_BRAIN_V3_TREND_MAX_ADJUST_PP", "2.0"))))
    adjustment = _clamp(raw_adjustment, min_pp / 100.0, max_pp / 100.0)

    if rate >= 0.72:
        label = "strong_support"
    elif rate >= 0.64:
        label = "supportive"
    elif rate <= 0.38:
        label = "caution"
    else:
        label = "neutral"

    combined_rows = _dedupe(home_rows + away_rows)
    scores365_rows = _source_rows(combined_rows, "365scores")
    scores365_rate = _over_rate(scores365_rows, line)
    sources = list(context.get("sources") or [])
    return {
        "available": True,
        "source": "recent_score_trend",
        "sources": sources,
        "scores365_trends_available": bool(context.get("has_trends") or context.get("has_top_trends")),
        "scores365_sample": len(scores365_rows),
        "scores365_over_rate": None if scores365_rate is None else round(scores365_rate, 4),
        "score": [home_score, away_score],
        "current_total": total_now,
        "period": period,
        "next_full_total_line": line,
        "home_over_rate": None if home_rate is None else round(home_rate, 4),
        "away_over_rate": None if away_rate is None else round(away_rate, 4),
        "combined_over_rate": round(float(rate), 4),
        "h2h_over_rate": None if h2h_rate is None else round(h2h_rate, 4),
        "h2h_weight": round(h2h_weight, 4),
        "pair_sample": pair_sample,
        "home_sample": home_n,
        "away_sample": away_n,
        "home_venue_sample": home_venue_n,
        "away_venue_sample": away_venue_n,
        "reliability": round(reliability, 4),
        "label": label,
        "raw_adjustment_pp": round(raw_adjustment * 100.0, 2),
        "adjustment_pp": round(adjustment * 100.0, 2),
    }


def apply_history_trend_support(record: dict[str, Any], decision: dict[str, Any]) -> dict[str, Any]:
    trend = score_aware_history_trend(record)
    decision["history_trend"] = trend
    if not bool(decision.get("active")) or not bool(trend.get("available")):
        return decision

    live_probability = _number(decision.get("live_probability"))
    current_probability = _number(decision.get("probability"))
    if live_probability is None or current_probability is None:
        trend["applied_adjustment_pp"] = 0.0
        trend["apply_reason"] = "live_probability_unavailable"
        return decision

    # History/trends are context only. They may help a READY live idea cross BET,
    # but they cannot alter a match that has not built a valid LIVE foundation.
    if not bool(decision.get("live_foundation")):
        trend["applied_adjustment_pp"] = 0.0
        trend["apply_reason"] = "live_foundation_required"
        return decision

    existing_context_pp = (current_probability - live_probability) * 100.0
    requested_pp = float(trend.get("adjustment_pp") or 0.0)
    total_min_pp = max(-3.0, min(0.0, float(os.getenv("GOOL_BRAIN_V3_CONTEXT_MIN_ADJUST_PP", "-2.0"))))
    total_max_pp = min(5.0, max(0.0, float(os.getenv("GOOL_BRAIN_V3_CONTEXT_MAX_ADJUST_PP", "4.0"))))
    total_context_pp = _clamp(existing_context_pp + requested_pp, total_min_pp, total_max_pp)
    adjusted = live_probability + total_context_pp / 100.0
    cap = _number(decision.get("confidence_cap"))
    if cap is not None:
        adjusted = min(adjusted, cap)
    adjusted = _clamp(adjusted, 0.01, 0.99)

    trend["existing_context_pp"] = round(existing_context_pp, 2)
    trend["total_context_pp"] = round(total_context_pp, 2)
    trend["applied_adjustment_pp"] = round((adjusted - current_probability) * 100.0, 2)
    trend["apply_reason"] = "live_confirmed_context"
    decision["probability"] = round(adjusted, 4)
    decision["confidence_score"] = round(adjusted * 100.0, 1)

    thoughts = list(decision.get("thoughts") or [])
    if abs(float(trend.get("applied_adjustment_pp") or 0.0)) >= 0.05:
        thoughts.append(
            f"history_trend={trend.get('label')} O{float(trend.get('next_full_total_line') or 0):.1f} "
            f"{float(trend.get('combined_over_rate') or 0)*100:.0f}% ({float(trend.get('applied_adjustment_pp') or 0):+.1f}pp)"
        )
    decision["thoughts"] = thoughts

    blocks = [str(row) for row in list(decision.get("blocks") or []) if str(row) not in _DYNAMIC_BLOCKS]
    bet_min = float(_number(decision.get("bet_min")) or 0.70)
    ready_min = float(_number(decision.get("ready_min")) or 0.60)
    bet_ready = bool(
        decision.get("live_foundation")
        and decision.get("sustained_pressure")
        and "brain_v3_pressure_falling" not in blocks
    )
    if adjusted >= bet_min and bet_ready:
        status = "BET"
    elif adjusted >= ready_min:
        status = "READY"
    else:
        status = "WATCH"
    if status != "BET":
        if adjusted < bet_min:
            blocks.append("brain_v3_probability_below_bet")
        elif not bet_ready:
            blocks.append("brain_v3_wait_confirmation")
    decision["status"] = status
    decision["blocks"] = list(dict.fromkeys(blocks))
    return decision


def _install_learning_hooks() -> None:
    """Let the existing learner measure whether trend-supported bets really work."""
    try:
        from . import brain_v3_learning as learning_module
    except Exception:
        return
    if getattr(learning_module, "_history_trend_hooks_installed", False):
        return

    original_pattern_keys = learning_module._pattern_keys
    original_diagnose = learning_module._diagnose

    def pattern_keys_with_history_trend(brain: dict[str, Any]) -> list[str]:
        keys = list(original_pattern_keys(brain))
        trend = brain.get("history_trend") or {}
        if bool(trend.get("available")):
            period = str(brain.get("period") or trend.get("period") or "UNK")
            label = str(trend.get("label") or "unknown")
            line = _number(trend.get("next_full_total_line"))
            if line is not None:
                keys.append(f"history_trend:{period}:O{line:.1f}:{label}")
            if int(trend.get("scores365_sample") or 0) >= 4:
                keys.append(f"scores365_trend:{period}:{label}")
        return list(dict.fromkeys(keys))

    def diagnose_with_history_trend(
        row: dict[str, Any],
        brain: dict[str, Any],
        record: dict[str, Any] | None,
    ) -> tuple[list[str], dict[str, Any]]:
        reasons, post = original_diagnose(row, brain, record)
        trend = brain.get("history_trend") or {}
        applied = float(_number(trend.get("applied_adjustment_pp")) or 0.0)
        outcome = str(row.get("result") or "").lower()
        if applied >= 0.75 and outcome == "lost":
            reasons.append("history_trend_support_did_not_convert")
        elif applied >= 0.75 and outcome == "won":
            reasons.append("history_trend_support_confirmed")
        elif applied <= -0.75 and outcome == "won":
            reasons.append("history_trend_caution_was_too_strong")
        return list(dict.fromkeys(reasons)), post

    learning_module._pattern_keys = pattern_keys_with_history_trend
    learning_module._diagnose = diagnose_with_history_trend
    learning_module._history_trend_hooks_installed = True


def install_brain_v3_history_trends() -> None:
    global _INSTALLED
    if _INSTALLED:
        return
    from . import brain_v3_decision as decision_module

    original_evaluate: Callable[..., dict[str, Any]] = decision_module.evaluate_brain_v3

    def evaluate_with_history_trends(
        record: dict[str, Any],
        *,
        data_quality: float,
        prematch_profile: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        decision = original_evaluate(
            record,
            data_quality=data_quality,
            prematch_profile=prematch_profile,
        )
        return apply_history_trend_support(record, decision)

    decision_module.evaluate_brain_v3 = evaluate_with_history_trends
    _install_learning_hooks()
    _INSTALLED = True
    print(
        "GOOL_BRAIN_V3_HISTORY_TRENDS installed mode=score_aware context_only max=+2.0pp total_context_cap=+4.0pp learning=tracked",
        flush=True,
    )


__all__ = [
    "apply_history_trend_support",
    "install_brain_v3_history_trends",
    "score_aware_history_trend",
]
