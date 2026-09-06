from __future__ import annotations

from typing import Any

from .browser_context_store import context_for_record


_DYNAMIC_BLOCKS = {"brain_v3_probability_below_bet", "brain_v3_wait_confirmation"}


def _number(value: Any) -> float | None:
    try:
        if value in (None, "", "-"):
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _clamp(value: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, float(value)))


def _goal_total_hint(record: dict[str, Any], browser: dict[str, Any]) -> dict[str, Any]:
    match = record.get("match") or {}
    current_total = int(match.get("home_score") or 0) + int(match.get("away_score") or 0)
    line = current_total + 0.5
    target = f"{line:.1f}"
    over: list[tuple[float, str]] = []
    under: list[tuple[float, str]] = []
    for row in browser.get("trends") or []:
        if not isinstance(row, dict):
            continue
        text = " ".join(str(row.get(k) or "") for k in ("text", "cause", "betCTA")).casefold()
        pct = _number(row.get("percentage"))
        if pct is None or pct < 0.0 or pct > 1.0:
            continue
        if f"over {target}" in text:
            over.append((pct, str(row.get("text") or row.get("cause") or "")))
        elif f"under {target}" in text:
            under.append((pct, str(row.get("text") or row.get("cause") or "")))

    best_over = max(over, default=(0.0, ""), key=lambda item: item[0])
    best_under = max(under, default=(0.0, ""), key=lambda item: item[0])
    over_pp = max(0.0, (best_over[0] - 0.55) * 5.0) if best_over[0] >= 0.62 else 0.0
    under_pp = max(0.0, (best_under[0] - 0.55) * 5.0) if best_under[0] >= 0.62 else 0.0
    raw_pp = _clamp(over_pp - under_pp, -1.25, 1.25)
    if raw_pp > 0.05:
        label = "supportive"
    elif raw_pp < -0.05:
        label = "caution"
    else:
        label = "neutral"
    return {
        "available": bool(over or under),
        "source": "365scores_chromium_trends",
        "current_total": current_total,
        "next_total_line": line,
        "market_hint": f"over_{target.replace('.', '_')}",
        "over_rate": None if not over else round(best_over[0], 4),
        "under_rate": None if not under else round(best_under[0], 4),
        "over_text": best_over[1][:220],
        "under_text": best_under[1][:220],
        "requested_adjustment_pp": round(raw_pp, 2),
        "label": label,
        "stats_keys": sorted(str(key) for key in (browser.get("stats") or {}).keys()),
        "visible_terms": list(browser.get("visible_terms") or []),
        "scores365_game_id": browser.get("scores365_game_id"),
        "page_url": browser.get("page_url"),
    }


def apply_browser_support(record: dict[str, Any], experts: dict[str, Any], decision: dict[str, Any]) -> dict[str, Any]:
    """Use one fresh Chromium observation as capped support, never as the engine."""
    if not isinstance(decision, dict) or not bool(decision.get("active")):
        return decision
    browser = context_for_record(record)
    if not isinstance(browser, dict):
        return decision

    support = _goal_total_hint(record, browser)
    decision["browser365"] = support
    if str(decision.get("period") or "") != "2H" or not bool(support.get("available")):
        return decision

    probability = _number(decision.get("probability"))
    if probability is None:
        return decision

    prematch_pp = float(_number(((decision.get("prematch") or {}).get("adjustment_pp"))) or 0.0)
    historical_pp = float(_number(((decision.get("external_trends") or {}).get("effective_adjustment_pp"))) or 0.0)
    already_pp = prematch_pp + historical_pp
    requested_pp = float(_number(support.get("requested_adjustment_pp")) or 0.0)
    combined_pp = _clamp(already_pp + requested_pp, -2.0, 4.0)
    effective_pp = combined_pp - already_pp
    support["effective_adjustment_pp"] = round(effective_pp, 2)
    support["combined_history_adjustment_pp"] = round(combined_pp, 2)
    if abs(effective_pp) < 1e-9:
        return decision

    adjusted = probability + effective_pp / 100.0
    cap = _number(decision.get("confidence_cap"))
    if cap is not None:
        adjusted = min(adjusted, cap)
    adjusted = _clamp(adjusted, 0.01, 0.99)
    decision["probability_before_browser365"] = round(probability, 4)
    decision["probability"] = round(adjusted, 4)
    decision["confidence_score"] = round(adjusted * 100.0, 1)

    thoughts = list(decision.get("thoughts") or [])
    over_rate = support.get("over_rate")
    under_rate = support.get("under_rate")
    if over_rate is not None:
        thoughts.append(f"365_chrome_over={float(over_rate)*100:.0f}% ({effective_pp:+.1f}pp)")
    elif under_rate is not None:
        thoughts.append(f"365_chrome_under={float(under_rate)*100:.0f}% ({effective_pp:+.1f}pp)")
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


__all__ = ["apply_browser_support"]
