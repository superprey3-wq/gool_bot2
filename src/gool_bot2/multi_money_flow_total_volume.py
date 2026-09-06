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
    for label in ("15s", "30s", "60s"):
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
    """Add total matched-volume intelligence without weakening existing safety gates.

    The normal FLOW path still detects fast matched-money bursts. This layer also
    recognizes a different pattern: a non-top competition whose goal-total market
    has accumulated unusually large matched volume. Total market volume alone has
    no direction, so a BIG_VOLUME signal still requires Over fair-probability to
    be moving upward. Existing post-goal, liquidity, spread, order-book and price
    guards are never bypassed because only the ordinary threshold miss can be
    promoted.
    """
    strategy, context, exchange = _flow_context(record)
    if not context:
        return result

    market_volume = max(0.0, _number(context.get("volume")))
    meta = _volume_meta(record, market_volume)

    # Existing valid short-window FLOW remains valid, but total volume now adds
    # information to its score. A huge non-top book is more meaningful than the
    # same absolute amount in a Champions League market.
    if bool(result.get("eligible")):
        enriched = dict(result)
        enriched.update(meta)
        if meta["league_tier"] == "non_top" and bool(meta["total_volume_anomaly"]):
            bonus = min(8.0, 2.0 + max(0.0, float(meta["volume_multiple"]) - 1.0) * 3.0)
            enriched["score"] = round(min(99.0, _number(enriched.get("score")) + bonus), 1)
            enriched["total_volume_bonus"] = round(bonus, 1)
        return enriched

    # Never revive a market rejected for safety, stale score, post-goal reset,
    # spread, price, liquidity or an unconfirmed/spoof-like order book.
    if str(result.get("reason") or "") != "money_flow_threshold_not_reached":
        return result

    # The requested anomaly lane is intentionally for non-top competitions.
    # Large absolute turnover is routine in elite leagues and should not by
    # itself become a special signal there.
    if meta["league_tier"] != "non_top" or not bool(meta["total_volume_anomaly"]):
        enriched = dict(result)
        enriched.update(meta)
        return enriched

    flow = context.get("flow") or {}
    direction = _direction_snapshot(flow, market_volume)
    if direction is None:
        enriched = dict(result)
        enriched.update(meta)
        enriched["reason"] = "money_flow_total_volume_wait_direction"
        return enriched

    min_direction_pp = _threshold("MATCHBOOK_FLOW_NON_TOP_TOTAL_MIN_FAIR_PP", 0.75)
    if float(direction["fair_delta_pp"]) < min_direction_pp:
        enriched = dict(result)
        enriched.update(meta)
        enriched["reason"] = "money_flow_total_volume_wait_direction"
        enriched["direction_snapshot"] = direction
        return enriched

    # If an order book exists, the base evaluator has already rejected this
    # record unless it was confirmed. Keep the confirmation explicit in output.
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

    extreme = market_volume >= float(meta["extreme_total_volume_threshold"])
    ratio = float(meta["volume_multiple"])
    back_wom = _number(flow.get("back_wom"), 0.5)
    orderflow_imbalance = _number(flow.get("orderflow_imbalance"))
    orderbook_streak = int(_number(flow.get("orderbook_support_streak")))
    score = min(
        99.0,
        80.0
        + min(9.0, max(0.0, ratio - 1.0) * 4.0)
        + min(6.0, max(0.0, float(direction["fair_delta_pp"])) * 2.0)
        + min(2.0, max(0.0, back_wom - 0.50) * 10.0)
        + min(2.0, max(0.0, orderflow_imbalance) * 2.0)
        + min(1.5, max(0, orderbook_streak - 1) * 0.5),
    )
    if extreme:
        score = max(score, 90.0)

    previous_fair = max(0.0, fair_over - float(direction["fair_delta_pp"]) / 100.0)
    return {
        "eligible": True,
        "level": "NON_TOP_EXTREME_VOLUME" if extreme else "NON_TOP_BIG_VOLUME",
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
        "signal_basis": "non_top_total_volume",
        **meta,
        **direction,
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
        f"direction={_threshold('MATCHBOOK_FLOW_NON_TOP_TOTAL_MIN_FAIR_PP', 0.75):.2f}pp",
        flush=True,
    )


__all__ = [
    "enhance_money_flow_result",
    "evaluate_money_flow_with_total_volume",
    "install_money_flow_total_volume",
]
