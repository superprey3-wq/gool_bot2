from __future__ import annotations

import os
from typing import Any, Callable

from . import multi_money_flow as money_flow


_TOP_LEAGUE_MARKERS = (
    "premier league",
    "laliga",
    "la liga",
    "serie a",
    "bundesliga",
    "ligue 1",
    "champions league",
    "europa league",
    "conference league",
)

_INSTALLED = False
_ORIGINAL_EVALUATE: Callable[[dict[str, Any]], dict[str, Any]] | None = None


def _number(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return float(default)


def _threshold(name: str, default: float) -> float:
    try:
        return max(0.0, float(os.getenv(name, str(default))))
    except (TypeError, ValueError):
        return float(default)


def _top_league(league: Any) -> bool:
    text = str(league or "").casefold()
    return any(marker in text for marker in _TOP_LEAGUE_MARKERS)


def _flow_context(record: dict[str, Any]) -> tuple[str, dict[str, Any], dict[str, Any]]:
    exchange = record.get("matchbook_exchange") or {}
    systems = exchange.get("systems") or {}
    strategy = money_flow._active_system(record)  # type: ignore[attr-defined]
    context = dict(systems.get(strategy) or {}) if strategy else {}
    if not context:
        minute = int((record.get("match") or {}).get("minute") or 0)
        legacy_key = "goal_before_ht" if minute <= 45 else "another_goal"
        context = dict(systems.get(legacy_key) or {})
        strategy = strategy or legacy_key
    return str(strategy or "money_flow"), context, exchange


def _direction_snapshot(flow: dict[str, Any], market_volume: float) -> dict[str, Any] | None:
    rows: list[dict[str, Any]] = []
    for label in ("15s", "30s", "60s", "120s", "300s"):
        if not bool(flow.get(f"window_ready_{label}")):
            continue
        delta = max(0.0, _number(flow.get(f"volume_delta_{label}")))
        fair_pp = _number(flow.get(f"fair_over_delta_pp_{label}"))
        prior_volume = max(1.0, market_volume - delta)
        rows.append(
            {
                "window": label,
                "volume_delta": delta,
                "fair_delta_pp": fair_pp,
                "relative_pct": delta / prior_volume * 100.0,
            }
        )
    if not rows:
        return None
    return max(rows, key=lambda row: (float(row["fair_delta_pp"]), float(row["volume_delta"])))


def _long_accumulation(flow: dict[str, Any], market_volume: float) -> dict[str, Any] | None:
    rows: list[dict[str, Any]] = []
    for label in ("120s", "300s"):
        if not bool(flow.get(f"window_ready_{label}")):
            continue
        delta = max(0.0, _number(flow.get(f"volume_delta_{label}")))
        fair_pp = _number(flow.get(f"fair_over_delta_pp_{label}"))
        prior_volume = max(1.0, market_volume - delta)
        rows.append({
            "window": label,
            "volume_delta": delta,
            "fair_delta_pp": fair_pp,
            "relative_pct": delta / prior_volume * 100.0,
        })
    if not rows:
        return None
    return max(rows, key=lambda row: (float(row["fair_delta_pp"]), float(row["relative_pct"])))


def _volume_meta(record: dict[str, Any], market_volume: float) -> dict[str, Any]:
    league = str(((record.get("match") or {}).get("league") or ""))
    top = _top_league(league)
    threshold = _threshold(
        "MATCHBOOK_FLOW_TOP_TOTAL_VOLUME_GBP" if top else "MATCHBOOK_FLOW_NON_TOP_TOTAL_VOLUME_GBP",
        25000.0 if top else 5000.0,
    )
    extreme = _threshold(
        "MATCHBOOK_FLOW_TOP_EXTREME_TOTAL_VOLUME_GBP" if top else "MATCHBOOK_FLOW_NON_TOP_EXTREME_TOTAL_VOLUME_GBP",
        75000.0 if top else 10000.0,
    )
    multiple = market_volume / max(1.0, threshold)
    return {
        "league_tier": "top" if top else "non_top",
        "total_volume_threshold": threshold,
        "extreme_total_volume_threshold": extreme,
        "volume_multiple": multiple,
        "total_volume_anomaly": bool(market_volume >= threshold),
    }


def enhance_money_flow_result(record: dict[str, Any], result: dict[str, Any]) -> dict[str, Any]:
    """Detect fast FLOW plus unusual accumulated turnover in non-top leagues.

    Short-window matched money remains the primary FLOW path. The anomaly lane
    separately recognizes a lower-profile competition whose goal-total market has
    built unusually large total turnover or sustained two/five-minute accumulation.
    Total money never supplies direction by itself: Over fair probability must be
    rising and, when long-window data exists, that direction must be persistent.
    """
    strategy, context, exchange = _flow_context(record)
    if not context:
        return result

    market_volume = max(0.0, _number(context.get("volume")))
    meta = _volume_meta(record, market_volume)
    flow = context.get("flow") or {}
    long_row = _long_accumulation(flow, market_volume)

    if bool(result.get("eligible")):
        enriched = dict(result)
        enriched.update(meta)
        bonus = 0.0
        if meta["league_tier"] == "non_top" and bool(meta["total_volume_anomaly"]):
            bonus += min(8.0, 2.0 + max(0.0, float(meta["volume_multiple"]) - 1.0) * 3.0)
        if long_row is not None and float(long_row["fair_delta_pp"]) >= 0.75 and float(long_row["relative_pct"]) >= 10.0:
            bonus += min(3.0, 1.0 + float(long_row["fair_delta_pp"]))
            enriched["long_accumulation"] = long_row
        if bonus > 0:
            enriched["score"] = round(min(99.0, _number(enriched.get("score")) + bonus), 1)
            enriched["total_volume_bonus"] = round(bonus, 1)
        return enriched

    if str(result.get("reason") or "") != "money_flow_threshold_not_reached":
        return result

    if meta["league_tier"] != "non_top":
        enriched = dict(result)
        enriched.update(meta)
        return enriched

    direction = _direction_snapshot(flow, market_volume)
    total_min_direction = _threshold("MATCHBOOK_FLOW_NON_TOP_TOTAL_MIN_FAIR_PP", 0.75)
    long_min_direction = _threshold("MATCHBOOK_FLOW_NON_TOP_LONG_MIN_FAIR_PP", 0.55)
    long_min_relative = _threshold("MATCHBOOK_FLOW_NON_TOP_LONG_MIN_RELATIVE_PCT", 12.0)
    long_min_volume = _threshold("MATCHBOOK_FLOW_NON_TOP_LONG_MIN_VOLUME_GBP", 1000.0)
    long_threshold_fraction = _threshold("MATCHBOOK_FLOW_NON_TOP_LONG_THRESHOLD_FRACTION", 0.70)
    long_consistency_min = _threshold("MATCHBOOK_FLOW_NON_TOP_LONG_DIRECTIONAL_CONSISTENCY", 0.67)
    long_consistency = _number(flow.get("long_direction_consistency"), 1.0)

    qualifies_total = bool(
        bool(meta["total_volume_anomaly"])
        and direction is not None
        and float(direction["fair_delta_pp"]) >= total_min_direction
    )
    qualifies_long = bool(
        long_row is not None
        and market_volume >= max(long_min_volume, float(meta["total_volume_threshold"]) * long_threshold_fraction)
        and float(long_row["fair_delta_pp"]) >= long_min_direction
        and float(long_row["relative_pct"]) >= long_min_relative
        and long_consistency >= long_consistency_min
    )

    if not qualifies_total and not qualifies_long:
        enriched = dict(result)
        enriched.update(meta)
        enriched["reason"] = (
            "money_flow_total_volume_wait_direction"
            if bool(meta["total_volume_anomaly"]) or long_row is not None
            else "money_flow_threshold_not_reached"
        )
        if direction is not None:
            enriched["direction_snapshot"] = direction
        if long_row is not None:
            enriched["long_accumulation"] = long_row
            enriched["long_direction_consistency"] = long_consistency
        return enriched

    if bool(flow.get("orderbook_ready")) and not bool(flow.get("orderbook_confirmed")):
        return result

    period = str(context.get("period") or "FT")
    family = "first_half_total" if period == "1H" else "match_total"
    fair_over = _number(context.get("fair_over"), -1.0)
    over = context.get("over") or {}
    best_back = _number((over.get("best_back") or {}).get("odds"))
    best_lay = _number((over.get("best_lay") or {}).get("odds"))
    if fair_over <= 0.0 or best_back <= 1.0:
        return result

    chosen = long_row if qualifies_long and (
        not qualifies_total or float(long_row.get("fair_delta_pp") or 0.0) >= float((direction or {}).get("fair_delta_pp") or 0.0)
    ) else direction
    if chosen is None:
        return result

    extreme = market_volume >= float(meta["extreme_total_volume_threshold"])
    ratio = float(meta["volume_multiple"])
    back_wom = _number(flow.get("back_wom"), 0.5)
    orderflow_imbalance = _number(flow.get("orderflow_imbalance"))
    orderbook_streak = int(_number(flow.get("orderbook_support_streak")))
    score = min(
        99.0,
        80.0
        + min(9.0, max(0.0, ratio - 1.0) * 4.0)
        + min(6.0, max(0.0, float(chosen["fair_delta_pp"])) * 2.0)
        + min(4.0, max(0.0, float(chosen["relative_pct"]) - 8.0) * 0.15)
        + min(2.0, max(0.0, back_wom - 0.50) * 10.0)
        + min(2.0, max(0.0, orderflow_imbalance) * 2.0)
        + min(1.5, max(0, orderbook_streak - 1) * 0.5),
    )
    if extreme:
        score = max(score, 90.0)

    if extreme:
        level = "NON_TOP_EXTREME_VOLUME"
    elif qualifies_long and str(chosen.get("window")) in {"120s", "300s"}:
        level = "NON_TOP_ACCUMULATION"
    else:
        level = "NON_TOP_BIG_VOLUME"

    previous_fair = max(0.0, fair_over - float(chosen["fair_delta_pp"]) / 100.0)
    return {
        "eligible": True,
        "level": level,
        "score": round(score, 1),
        "strategy": "money_flow",
        "reference_strategy": strategy,
        "period": period,
        "market_family": family,
        "line": _number(context.get("line")),
        "odd": best_back,
        "lay_odd": best_lay,
        "fair_over": fair_over,
        "previous_fair_over": previous_fair,
        "market_volume": market_volume,
        "market_id": context.get("market_id"),
        "market_name": context.get("market_name"),
        "matchbook_event_id": ((exchange.get("event") or {}).get("id")),
        "orderbook_ready": bool(flow.get("orderbook_ready")),
        "orderbook_confirmed": bool(flow.get("orderbook_confirmed")),
        "orderbook_confirmations": int(_number(flow.get("orderbook_confirmation_count"))),
        "back_wom": back_wom,
        "book_imbalance": _number(flow.get("book_imbalance")),
        "orderflow_imbalance": orderflow_imbalance,
        "orderbook_support_streak": orderbook_streak,
        "back_depth_weighted": _number(flow.get("back_depth_weighted")),
        "lay_depth_weighted": _number(flow.get("lay_depth_weighted")),
        "long_direction_consistency": long_consistency,
        "signal_basis": "non_top_long_accumulation" if level == "NON_TOP_ACCUMULATION" else "non_top_total_volume",
        **meta,
        **chosen,
    }


def evaluate_money_flow_with_total_volume(record: dict[str, Any]) -> dict[str, Any]:
    if _ORIGINAL_EVALUATE is None:
        return enhance_money_flow_result(record, money_flow.evaluate_money_flow(record))
    return enhance_money_flow_result(record, _ORIGINAL_EVALUATE(record))


def install_money_flow_total_volume() -> None:
    global _INSTALLED, _ORIGINAL_EVALUATE
    if _INSTALLED:
        return
    _ORIGINAL_EVALUATE = money_flow.evaluate_money_flow
    money_flow.evaluate_money_flow = evaluate_money_flow_with_total_volume
    _INSTALLED = True
    print(
        "GOOL_MONEY_FLOW_TOTAL_VOLUME installed "
        f"non_top=£{_threshold('MATCHBOOK_FLOW_NON_TOP_TOTAL_VOLUME_GBP', 5000.0):.0f} "
        f"non_top_extreme=£{_threshold('MATCHBOOK_FLOW_NON_TOP_EXTREME_TOTAL_VOLUME_GBP', 10000.0):.0f} "
        f"direction={_threshold('MATCHBOOK_FLOW_NON_TOP_TOTAL_MIN_FAIR_PP', 0.75):.2f}pp "
        f"long=120/300s/{_threshold('MATCHBOOK_FLOW_NON_TOP_LONG_MIN_RELATIVE_PCT', 12.0):.0f}%",
        flush=True,
    )


__all__ = [
    "enhance_money_flow_result",
    "evaluate_money_flow_with_total_volume",
    "install_money_flow_total_volume",
]
