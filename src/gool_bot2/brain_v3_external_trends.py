from __future__ import annotations

import os
import time
from typing import Any

from .providers import Scores365Provider


_PROVIDER: Scores365Provider | None = None
_CACHE: dict[str, tuple[float, dict[str, Any] | None]] = {}
_DYNAMIC_BLOCKS = {"brain_v3_probability_below_bet", "brain_v3_wait_confirmation"}
_LINES = (0.5, 1.5, 2.5, 3.5, 4.5)


def _number(value: Any) -> float | None:
    try:
        if value in (None, "", "-"):
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _clamp(value: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, float(value)))


def _provider() -> Scores365Provider:
    global _PROVIDER
    if _PROVIDER is None:
        _PROVIDER = Scores365Provider()
    return _PROVIDER


def _full_score(row: dict[str, Any]) -> tuple[int, int] | None:
    home = _number(row.get("home_score"))
    away = _number(row.get("away_score"))
    if home is None or away is None or home < 0 or away < 0:
        return None
    return int(home), int(away)


def _identity(row: dict[str, Any]) -> tuple[Any, ...]:
    event = str(row.get("event_id") or "").strip()
    if event:
        return ("event", event)
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
        if not isinstance(row, dict) or _full_score(row) is None:
            continue
        key = _identity(row)
        if key in seen:
            continue
        seen.add(key)
        out.append(row)
    return out


def _profile(rows: list[dict[str, Any]]) -> dict[str, Any]:
    usable = _dedupe(rows)
    totals: list[int] = []
    btts = 0
    for row in usable:
        score = _full_score(row)
        if score is None:
            continue
        total = score[0] + score[1]
        totals.append(total)
        if score[0] > 0 and score[1] > 0:
            btts += 1
    count = len(totals)
    if not count:
        return {"matches": 0, "avg_total": None, "btts_rate": None, "over": {f"{line:.1f}": None for line in _LINES}}
    return {
        "matches": count,
        "avg_total": round(sum(totals) / count, 3),
        "btts_rate": round(btts / count, 4),
        "over": {f"{line:.1f}": round(sum(1 for total in totals if total > line) / count, 4) for line in _LINES},
    }


def _context_rows(context: dict[str, Any]) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    home = [dict(row) for row in (context.get("home_recent") or []) if isinstance(row, dict)]
    away = [dict(row) for row in (context.get("away_recent") or []) if isinstance(row, dict)]
    h2h = [dict(row) for row in (context.get("h2h") or []) if isinstance(row, dict)]
    return home, away, h2h


def _trend_from_context(record: dict[str, Any], context: dict[str, Any]) -> dict[str, Any]:
    match = record.get("match") or {}
    hs = int(_number(match.get("home_score")) or 0)
    aws = int(_number(match.get("away_score")) or 0)
    current_total = max(0, hs + aws)
    line = current_total + 0.5
    if line > 4.5:
        return {"available": False, "reason": "next_total_line_above_supported_history", "current_total": current_total}

    home_rows, away_rows, h2h_rows = _context_rows(context)
    home = _profile(home_rows)
    away = _profile(away_rows)
    h2h = _profile(h2h_rows)
    key = f"{line:.1f}"
    home_rate = _number((home.get("over") or {}).get(key))
    away_rate = _number((away.get("over") or {}).get(key))
    rates = [value for value in (home_rate, away_rate) if value is not None]
    if not rates:
        return {"available": False, "reason": "full_match_history_unavailable", "current_total": current_total, "next_total_line": line}

    pair_sample = min(int(home.get("matches") or 0), int(away.get("matches") or 0)) if home_rate is not None and away_rate is not None else max(int(home.get("matches") or 0), int(away.get("matches") or 0))
    rate = sum(rates) / len(rates)
    h2h_rate = _number((h2h.get("over") or {}).get(key))
    h2h_n = int(h2h.get("matches") or 0)
    h2h_weight = min(0.15, 0.03 * h2h_n)
    if h2h_rate is not None and h2h_n >= 2:
        rate = (1.0 - h2h_weight) * rate + h2h_weight * h2h_rate
    reliability = min(1.0, max(0, pair_sample) / 8.0)

    raw = (rate - 0.55) * 0.08 * reliability
    adjustment = _clamp(raw, -0.010, 0.015)
    if rate >= 0.70 and reliability >= 0.50:
        label = "supportive"
    elif rate <= 0.42 and reliability >= 0.50:
        label = "caution"
    else:
        label = "neutral"
        adjustment *= 0.50

    return {
        "available": True,
        "source": "historical_goal_trends",
        "current_total": current_total,
        "next_total_line": line,
        "market_hint": f"over_{key.replace('.', '_')}",
        "rate": round(rate, 4),
        "home_rate": None if home_rate is None else round(home_rate, 4),
        "away_rate": None if away_rate is None else round(away_rate, 4),
        "h2h_rate": None if h2h_rate is None else round(h2h_rate, 4),
        "pair_sample": pair_sample,
        "h2h_sample": h2h_n,
        "reliability": round(reliability, 3),
        "adjustment_pp": round(adjustment * 100.0, 2),
        "label": label,
        "home_profile": home,
        "away_profile": away,
    }


def _lazy_scores365_context(record: dict[str, Any], decision: dict[str, Any]) -> dict[str, Any] | None:
    if str(decision.get("period") or "") != "2H" or not bool(decision.get("live_foundation")):
        return None
    p = _number(decision.get("probability"))
    ready = _number(decision.get("ready_min")) or 0.60
    if p is None or p < ready - 0.03:
        return None
    match = record.get("match") or {}
    home = str(match.get("home") or "").strip()
    away = str(match.get("away") or "").strip()
    if not home or not away:
        return None
    key = str(match.get("flashscore_event_id") or f"{home.casefold()}::{away.casefold()}")
    ttl = max(600.0, float(os.getenv("GOOL_BRAIN_V3_TREND_CACHE_SECONDS", "2700")))
    now = time.time()
    cached = _CACHE.get(key)
    if cached and now - cached[0] < ttl:
        return cached[1]
    try:
        limit = max(4, min(10, int(os.getenv("GOOL_BRAIN_V3_TREND_HISTORY_MATCHES", "6"))))
        method = getattr(_provider(), "half_prematch_context", None)
        extra = method(home, away, limit=limit) if callable(method) else None
    except Exception as exc:
        print(f"GOOL_BRAIN_V3_TREND_FETCH_ERROR match={key} error={type(exc).__name__}:{exc}", flush=True)
        extra = None
    _CACHE[key] = (now, dict(extra) if isinstance(extra, dict) else None)
    return _CACHE[key][1]


def build_external_trend_context(record: dict[str, Any], decision: dict[str, Any]) -> dict[str, Any]:
    if str(decision.get("period") or "") != "2H":
        return {"available": False, "reason": "full_match_total_trend_not_used_in_1H", "adjustment_pp": 0.0}
    context = dict(record.get("prematch_context") or {})
    trend = _trend_from_context(record, context)
    if bool(trend.get("available")) and int(trend.get("pair_sample") or 0) >= 3:
        trend["lazy_365_loaded"] = False
        return trend
    extra = _lazy_scores365_context(record, decision)
    if isinstance(extra, dict):
        trend = _trend_from_context(record, extra)
        trend["lazy_365_loaded"] = True
        trend["scores365_has_trends"] = bool(extra.get("has_trends"))
        trend["scores365_has_top_trends"] = bool(extra.get("has_top_trends"))
    trend.setdefault("adjustment_pp", 0.0)
    return trend


def apply_external_trend_context(record: dict[str, Any], experts: dict[str, Any], decision: dict[str, Any]) -> dict[str, Any]:
    """Add a small, score-relevant historical hint without weakening LIVE gates.

    Example: at 1-1 the relevant full-match trend is Over 2.5 because one more
    goal completes that line. Historical trend support can never create a signal
    without Brain V3's LIVE foundation and sustained-pressure confirmation.
    Combined PREMATCH + trend context remains capped at -2..+4 percentage points.
    """
    if not isinstance(decision, dict) or not bool(decision.get("active")):
        return decision
    trend = build_external_trend_context(record, decision)
    decision["external_trends"] = trend
    requested_pp = float(_number(trend.get("adjustment_pp")) or 0.0)
    prematch_pp = float(_number(((decision.get("prematch") or {}).get("adjustment_pp"))) or 0.0)
    combined_pp = _clamp(prematch_pp + requested_pp, -2.0, 4.0)
    effective_pp = combined_pp - prematch_pp
    trend["requested_adjustment_pp"] = round(requested_pp, 2)
    trend["effective_adjustment_pp"] = round(effective_pp, 2)
    trend["combined_history_adjustment_pp"] = round(combined_pp, 2)

    probability = _number(decision.get("probability"))
    if probability is None or abs(effective_pp) < 1e-9:
        return decision
    adjusted = probability + effective_pp / 100.0
    cap = _number(decision.get("confidence_cap"))
    if cap is not None:
        adjusted = min(adjusted, cap)
    adjusted = _clamp(adjusted, 0.01, 0.99)
    decision["probability_before_external_trends"] = round(probability, 4)
    decision["probability"] = round(adjusted, 4)
    decision["confidence_score"] = round(adjusted * 100.0, 1)

    thoughts = list(decision.get("thoughts") or [])
    if bool(trend.get("available")):
        thoughts.append(f"hist_trend={trend.get('market_hint')} {float(trend.get('rate') or 0)*100:.0f}% ({effective_pp:+.1f}pp)")
    decision["thoughts"] = thoughts

    blocks = [str(row) for row in list(decision.get("blocks") or []) if str(row) not in _DYNAMIC_BLOCKS]
    live_foundation = bool(decision.get("live_foundation"))
    bet_ready = bool(live_foundation and bool(decision.get("sustained_pressure")) and "brain_v3_pressure_falling" not in blocks)
    bet_min = float(_number(decision.get("bet_min")) or 0.70)
    ready_min = float(_number(decision.get("ready_min")) or 0.60)
    if not live_foundation:
        status = "WATCH"
    elif adjusted >= bet_min and bet_ready:
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
    record["brain_v3_decision"] = decision

    strategy = str(decision.get("strategy") or "")
    expert = experts.get(strategy) if strategy else None
    if isinstance(expert, dict) and str(expert.get("source") or "").startswith("brain_v3:"):
        expert["probability"] = round(adjusted, 4)
        expert["passed"] = status == "BET"
        expert["state"] = "PASS" if status == "BET" else "BORDERLINE"
        diagnostics = dict(expert.get("diagnostics") or {})
        diagnostics["brain_v3"] = decision
        expert["diagnostics"] = diagnostics
    return decision


__all__ = ["build_external_trend_context", "apply_external_trend_context"]
