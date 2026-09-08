from __future__ import annotations

import json
import os
import re
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from . import sxbet_public

TOTAL_MARKET_TYPE = 2


def _number(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return float(default)


def _line_from_market(market: dict[str, Any]) -> float | None:
    value = market.get("line")
    try:
        if value not in (None, ""):
            return float(value)
    except (TypeError, ValueError):
        pass
    for key in ("outcomeOneName", "outcomeTwoName"):
        match = re.search(r"([0-9]+(?:\.[0-9]+)?)", str(market.get(key) or ""))
        if match:
            return float(match.group(1))
    return None


def _over_is_outcome_one(market: dict[str, Any]) -> bool | None:
    one = str(market.get("outcomeOneName") or "").strip().casefold()
    two = str(market.get("outcomeTwoName") or "").strip().casefold()
    if one.startswith("over") and two.startswith("under"):
        return True
    if one.startswith("under") and two.startswith("over"):
        return False
    if "over" in one and "under" in two:
        return True
    if "under" in one and "over" in two:
        return False
    return None


def fetch_live_total_markets(*, timeout: float = 4.0, max_pages: int = 4) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    pagination_key: str | None = None
    for _ in range(max(1, max_pages)):
        params: dict[str, Any] = {
            "sportIds": str(sxbet_public.SOCCER_ID),
            "liveOnly": "true",
            "onlyMainLine": "true",
            "type": str(TOTAL_MARKET_TYPE),
            "pageSize": "100",
        }
        if pagination_key:
            params["paginationKey"] = pagination_key
        payload = sxbet_public._request_json("/markets/active", params, timeout=timeout)
        batch, pagination_key = sxbet_public._markets_from_payload(payload)
        rows.extend(batch)
        if not pagination_key:
            break
    return rows


def build_live_total_board(markets: list[dict[str, Any]], orders: list[dict[str, Any]]) -> list[dict[str, Any]]:
    orders_by_hash: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in orders:
        market_hash = str(row.get("marketHash") or "").strip()
        if market_hash:
            orders_by_hash[market_hash].append(row)

    result: list[dict[str, Any]] = []
    for market in markets:
        if int(_number(market.get("sportId"))) != sxbet_public.SOCCER_ID:
            continue
        if int(_number(market.get("type"))) != TOTAL_MARKET_TYPE:
            continue
        if market.get("mainLine") is False:
            continue

        market_hash = str(market.get("marketHash") or "").strip()
        home = str(market.get("teamOneName") or "").strip()
        away = str(market.get("teamTwoName") or "").strip()
        line = _line_from_market(market)
        over_is_one = _over_is_outcome_one(market)
        if not market_hash or not home or not away or line is None or over_is_one is None:
            continue

        book = orders_by_hash.get(market_hash, [])
        over = sxbet_public._taker_quote(book, over_is_one)
        under = sxbet_public._taker_quote(book, not over_is_one)
        over["market_hash"] = market_hash
        under["market_hash"] = market_hash
        liquidity = _number(over.get("available_usdc")) + _number(under.get("available_usdc"))
        if liquidity <= 0.0:
            continue

        event_id = str(market.get("sportXeventId") or "").strip()
        game_time = int(_number(market.get("gameTime")))
        result.append(
            {
                "event_id": event_id or f"{game_time}|{home}|{away}",
                "name": f"{home} — {away}",
                "home": home,
                "away": away,
                "start": game_time,
                "in_running": True,
                "line": float(line),
                "market_hash": market_hash,
                "liquidity_usdc": round(liquidity, 2),
                "outcomes": {"TB": over, "TM": under},
            }
        )

    result.sort(key=lambda row: _number(row.get("liquidity_usdc")), reverse=True)
    return result


def _snapshot_path() -> Path:
    raw = os.getenv("SXBET_TOTALS_SNAPSHOT_PATH", "").strip()
    if raw:
        return Path(raw)
    root = Path(os.getenv("RUNTIME_DATA_DIR", "data"))
    return root / "live" / "sxbet_totals_snapshot.json"


def _load_snapshot(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text("utf-8"))
        return payload if isinstance(payload, dict) else {}
    except Exception:
        return {}


def _save_snapshot(path: Path, payload: dict[str, Any]) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(json.dumps(payload, ensure_ascii=False, separators=(",", ":")), "utf-8")
        tmp.replace(path)
    except Exception:
        pass


def _flow(current: dict[str, Any], previous: dict[str, Any] | None) -> dict[str, Any]:
    if not previous:
        return {"ready": False, "level": "WARMING"}

    current_outcomes = current.get("outcomes") or {}
    previous_outcomes = previous.get("outcomes") or {}
    best_label = ""
    best_score = 0.0
    best: dict[str, Any] = {}

    for label in ("TB", "TM"):
        now = current_outcomes.get(label) or {}
        old = previous_outcomes.get(label) or {}
        now_prob = _number(now.get("probability"))
        old_prob = _number(old.get("probability"))
        now_depth = _number(now.get("available_usdc"))
        old_depth = _number(old.get("available_usdc"))
        if now_prob <= 0.0 or old_prob <= 0.0:
            continue
        delta_pp = (now_prob - old_prob) * 100.0
        depth_delta = now_depth - old_depth
        relative_depth = depth_delta / max(1.0, old_depth) * 100.0
        score = max(0.0, delta_pp) * 10.0 + max(0.0, relative_depth)
        if delta_pp >= 0.6 and depth_delta >= 25.0 and relative_depth >= 5.0 and score > best_score:
            best_label = label
            best_score = score
            best = {
                "implied_delta_pp": round(delta_pp, 3),
                "liquidity_delta_usdc": round(depth_delta, 2),
                "relative_liquidity_pct": round(relative_depth, 2),
                "old_odd": _number(old.get("decimal_odd")),
                "new_odd": _number(now.get("decimal_odd")),
            }

    if not best_label:
        return {"ready": True, "level": "NEUTRAL"}
    strong = best["implied_delta_pp"] >= 1.5 and best["liquidity_delta_usdc"] >= 100.0
    return {
        "ready": True,
        "level": "STRONG_LIQUIDITY_PUSH" if strong else "LIQUIDITY_PUSH",
        "outcome": best_label,
        **best,
    }


def fetch_sxbet_totals_state(*, timeout: float | None = None) -> dict[str, Any]:
    timeout = float(timeout if timeout is not None else os.getenv("SXBET_PUBLIC_TIMEOUT", "4"))
    path = _snapshot_path()
    previous_state = _load_snapshot(path)
    previous_by_event = {
        str(row.get("event_id") or ""): row
        for row in previous_state.get("events") or []
        if isinstance(row, dict)
    }
    try:
        markets = fetch_live_total_markets(timeout=timeout)
        hashes = [
            str(row.get("marketHash") or "").strip()
            for row in markets
            if isinstance(row, dict) and str(row.get("marketHash") or "").strip()
        ]
        orders = sxbet_public.fetch_orders(hashes, timeout=timeout)
        events = build_live_total_board(markets, orders)
        for event in events:
            event["flow"] = _flow(event, previous_by_event.get(str(event.get("event_id") or "")))
        state = {
            "captured_at": datetime.now(timezone.utc).isoformat(),
            "available": True,
            "source": "sxbet_public_rest_totals",
            "currency": "USDC",
            "market_type": TOTAL_MARKET_TYPE,
            "semantics": "executable_orderbook_liquidity_not_matched_volume",
            "markets_seen": len(markets),
            "orders_seen": len(orders),
            "events": events,
        }
        _save_snapshot(path, state)
        return state
    except Exception as exc:
        return {
            "captured_at": datetime.now(timezone.utc).isoformat(),
            "available": False,
            "source": "sxbet_public_rest_totals",
            "currency": "USDC",
            "market_type": TOTAL_MARKET_TYPE,
            "error": f"{type(exc).__name__}:{exc}",
            "events": [],
        }


__all__ = [
    "TOTAL_MARKET_TYPE",
    "build_live_total_board",
    "fetch_live_total_markets",
    "fetch_sxbet_totals_state",
]
