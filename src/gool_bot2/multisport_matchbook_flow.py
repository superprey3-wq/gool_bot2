from __future__ import annotations

import os
from datetime import datetime, timezone
from typing import Any

from .matchbook_exchange import load_matchbook_state
from .providers.common import pair_score


def _num(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return float(default)


def _captured_age_seconds(payload: dict[str, Any], now: datetime | None = None) -> float | None:
    raw = str(payload.get("captured_at") or "").strip()
    if not raw:
        return None
    try:
        stamp = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        if stamp.tzinfo is None:
            stamp = stamp.replace(tzinfo=timezone.utc)
        ref = now or datetime.now(timezone.utc)
        return max(0.0, (ref.astimezone(timezone.utc) - stamp.astimezone(timezone.utc)).total_seconds())
    except Exception:
        return None


def _scope_period(scope: str) -> str | None:
    raw = str(scope or "").upper()
    if raw == "FULL_MATCH":
        return "FT"
    if raw.startswith("QUARTER_") or raw.startswith("PERIOD_"):
        return raw
    return None


def _line_tolerance(sport: str) -> float:
    if str(sport).casefold() == "hockey":
        return max(0.0, _num(os.getenv("GOOL_HOCKEY_MATCHBOOK_LINE_TOLERANCE"), 0.75))
    return max(0.0, _num(os.getenv("GOOL_BASKETBALL_MATCHBOOK_LINE_TOLERANCE"), 4.0))


def _best_event(
    payload: dict[str, Any],
    *,
    sport: str,
    home: str,
    away: str,
) -> tuple[dict[str, Any] | None, float]:
    target_sport = str(sport or "").casefold()
    best: dict[str, Any] | None = None
    best_score = 0.0
    for raw in payload.get("events") or []:
        if not isinstance(raw, dict):
            continue
        event = dict(raw)
        event_sport = str(event.get("sport_key") or "").casefold()
        # For multisport money-flow we require explicit sport identity. This
        # prevents an old soccer-only state file from confirming a basketball
        # or hockey match with coincidentally similar team names.
        if event_sport != target_sport:
            continue
        score = pair_score(
            home,
            away,
            str(event.get("home") or ""),
            str(event.get("away") or ""),
        )
        if score > best_score:
            best = event
            best_score = score
    floor = max(0.0, min(1.0, _num(os.getenv("GOOL_MULTISPORT_MATCHBOOK_MATCH_MIN_SCORE"), 0.72)))
    return (best, best_score) if best is not None and best_score >= floor else (None, best_score)


def _closest_market(event: dict[str, Any], period: str, line: float, sport: str) -> dict[str, Any] | None:
    candidates: list[tuple[float, dict[str, Any]]] = []
    for raw in (event.get("totals") or {}).values():
        if not isinstance(raw, dict) or str(raw.get("period") or "") != period:
            continue
        try:
            market_line = float(raw.get("line"))
        except (TypeError, ValueError):
            continue
        gap = abs(market_line - float(line))
        candidates.append((gap, dict(raw)))
    if not candidates:
        return None
    gap, market = min(candidates, key=lambda row: row[0])
    if gap > _line_tolerance(sport):
        return None
    market["line_gap"] = round(gap, 3)
    return market


def _flow_window(flow: dict[str, Any]) -> tuple[str | None, float, float]:
    rows: list[tuple[str, float, float]] = []
    for label in ("30s", "60s"):
        if not bool(flow.get(f"window_ready_{label}")):
            continue
        rows.append(
            (
                label,
                max(0.0, _num(flow.get(f"volume_delta_{label}"))),
                _num(flow.get(f"fair_over_delta_pp_{label}")),
            )
        )
    if not rows:
        activity = max(0.0, _num(flow.get("activity_volume")))
        delta_pp = _num(flow.get("direction_pp"))
        return (None, activity, delta_pp)
    # Prefer the window with the largest actually matched increment; if tied,
    # prefer the larger fair-probability move.
    return max(rows, key=lambda row: (row[1], abs(row[2])))


def matchbook_money_flow(
    *,
    sport: str,
    home: str,
    away: str,
    scope: str,
    line: float,
    direction: str,
    state: dict[str, Any] | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Return real matched-volume confirmation for a multisport total.

    This function never creates a betting candidate. It is called only after the
    hockey/basketball model has independently produced one.
    """
    payload = dict(state) if isinstance(state, dict) else load_matchbook_state()
    if not payload or payload.get("available") is False:
        return {"available": False, "reason": "matchbook_state_unavailable"}

    age = _captured_age_seconds(payload, now)
    max_age = max(15.0, _num(os.getenv("GOOL_MULTISPORT_MATCHBOOK_MAX_AGE_SECONDS"), 90.0))
    if age is None or age > max_age:
        return {
            "available": False,
            "reason": "matchbook_state_stale",
            "age_seconds": None if age is None else round(age, 1),
        }

    period = _scope_period(scope)
    if period is None:
        return {"available": False, "reason": "matchbook_scope_unsupported"}

    event, match_score = _best_event(payload, sport=sport, home=home, away=away)
    if event is None:
        return {
            "available": False,
            "reason": "matchbook_event_unmatched",
            "match_score": round(match_score, 4),
        }

    market = _closest_market(event, period, float(line), sport)
    if market is None:
        return {
            "available": False,
            "reason": "matchbook_total_unmatched",
            "match_score": round(match_score, 4),
            "period": period,
        }

    flow = dict(market.get("flow") or {})
    window, volume_delta, fair_delta_pp = _flow_window(flow)
    if window is None and volume_delta <= 0.0 and abs(fair_delta_pp) < 1e-9:
        return {
            "available": False,
            "reason": "matchbook_flow_window_unready",
            "match_score": round(match_score, 4),
            "period": period,
            "market_line": market.get("line"),
        }

    flow_direction = "over" if fair_delta_pp > 0.0 else "under" if fair_delta_pp < 0.0 else "neutral"
    signal_direction = str(direction or "").casefold()
    agrees = flow_direction == signal_direction and flow_direction != "neutral"

    min_volume = max(0.0, _num(os.getenv("GOOL_MULTISPORT_MATCHBOOK_MIN_MATCHED_DELTA"), 20.0))
    min_pp = max(0.0, _num(os.getenv("GOOL_MULTISPORT_MATCHBOOK_MIN_FAIR_PP"), 1.25))
    strong_volume = max(min_volume, _num(os.getenv("GOOL_MULTISPORT_MATCHBOOK_STRONG_MATCHED_DELTA"), 75.0))
    strong_pp = max(min_pp, _num(os.getenv("GOOL_MULTISPORT_MATCHBOOK_STRONG_FAIR_PP"), 3.0))

    spoof = bool(flow.get("transient_liquidity_spike"))
    pulled = bool(flow.get("liquidity_pull"))
    confirmed = bool(
        not spoof
        and not pulled
        and volume_delta >= min_volume
        and abs(fair_delta_pp) >= min_pp
        and flow_direction != "neutral"
    )
    strong = bool(confirmed and volume_delta >= strong_volume and abs(fair_delta_pp) >= strong_pp)

    market_volume = max(0.0, _num(market.get("volume")))
    return {
        "available": True,
        "confirmed": confirmed,
        "strong": strong,
        "agrees": agrees if confirmed else None,
        "direction": flow_direction,
        "signal_direction": signal_direction,
        "window": window,
        "matched_volume_delta": round(volume_delta, 2),
        "market_volume": round(market_volume, 2),
        "fair_over_delta_pp": round(fair_delta_pp, 3),
        "fair_over": market.get("fair_over"),
        "level": flow.get("level"),
        "orderbook_ready": bool(flow.get("orderbook_ready")),
        "orderbook_confirmed_over": bool(flow.get("orderbook_confirmed")),
        "back_wom": flow.get("back_wom"),
        "orderflow_imbalance": flow.get("orderflow_imbalance"),
        "transient_liquidity_spike": spoof,
        "liquidity_pull": pulled,
        "period": period,
        "requested_line": round(float(line), 3),
        "market_line": market.get("line"),
        "line_gap": market.get("line_gap"),
        "market_id": market.get("id"),
        "market_name": market.get("name"),
        "event_id": event.get("event_id"),
        "event_name": event.get("name"),
        "match_score": round(match_score, 4),
        "state_age_seconds": None if age is None else round(age, 1),
        "source": "matchbook_matched_volume",
    }
