from __future__ import annotations

import json
import os
import time
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlencode
from urllib.request import Request, urlopen

API_URL = os.getenv("SXBET_PUBLIC_API_URL", "https://api.sx.bet").rstrip("/")
BASE_TOKEN = os.getenv(
    "SXBET_PUBLIC_BASE_TOKEN",
    "0x6629Ce1Cf35Cc1329ebB4F63202F3f197b3F050B",
)
SOCCER_ID = 5
ODDS_PRECISION = 10**20
USDC_DECIMALS = 1_000_000
ORDER_BATCH_SIZE = 30


def _number(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return float(default)


def _request_json(path: str, params: dict[str, Any], *, timeout: float = 4.0) -> Any:
    query = urlencode({key: value for key, value in params.items() if value not in (None, "")})
    url = f"{API_URL}{path}" + (f"?{query}" if query else "")
    req = Request(
        url,
        headers={
            "Accept": "application/json",
            "User-Agent": "GOOL-BOT2/2.0 SXBet-public-board",
        },
    )
    with urlopen(req, timeout=max(1.0, timeout)) as response:
        status = int(getattr(response, "status", 200))
        if status < 200 or status >= 300:
            raise RuntimeError(f"sxbet_http_{status}")
        return json.loads(response.read().decode("utf-8"))


def _data(payload: Any) -> Any:
    if isinstance(payload, dict) and "data" in payload:
        return payload["data"]
    return payload


def _markets_from_payload(payload: Any) -> tuple[list[dict[str, Any]], str | None]:
    body = _data(payload)
    if isinstance(body, dict):
        rows = body.get("markets") or []
        next_key = body.get("nextKey") or body.get("paginationKey")
    elif isinstance(body, list):
        rows = body
        next_key = None
    else:
        rows = []
        next_key = None
    return [dict(row) for row in rows if isinstance(row, dict)], str(next_key) if next_key else None


def fetch_live_soccer_markets(*, timeout: float = 4.0, max_pages: int = 4) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    pagination_key: str | None = None
    for _ in range(max(1, max_pages)):
        params: dict[str, Any] = {
            "sportIds": str(SOCCER_ID),
            "liveOnly": "true",
            "onlyMainLine": "true",
            "pageSize": "100",
        }
        if pagination_key:
            params["paginationKey"] = pagination_key
        payload = _request_json("/markets/active", params, timeout=timeout)
        batch, pagination_key = _markets_from_payload(payload)
        rows.extend(batch)
        if not pagination_key:
            break
    return rows


def fetch_orders(market_hashes: list[str], *, timeout: float = 4.0) -> list[dict[str, Any]]:
    hashes = [str(value).strip() for value in market_hashes if str(value).strip()]
    if not hashes:
        return []
    rows: list[dict[str, Any]] = []
    for offset in range(0, len(hashes), ORDER_BATCH_SIZE):
        batch = hashes[offset : offset + ORDER_BATCH_SIZE]
        payload = _request_json(
            "/orders",
            {"marketHashes": ",".join(batch), "baseToken": BASE_TOKEN},
            timeout=timeout,
        )
        body = _data(payload)
        if isinstance(body, list):
            rows.extend(dict(row) for row in body if isinstance(row, dict))
    return rows


def _taker_quote(orders: list[dict[str, Any]], taker_bets_outcome_one: bool) -> dict[str, Any]:
    levels: dict[float, float] = defaultdict(float)
    total = 0.0
    for row in orders:
        maker_on_one = bool(row.get("isMakerBettingOutcomeOne"))
        if maker_on_one == taker_bets_outcome_one:
            continue
        try:
            pct_raw = int(str(row.get("percentageOdds") or "0"))
            total_raw = int(str(row.get("totalBetSize") or "0"))
            filled_raw = int(str(row.get("fillAmount") or "0"))
        except (TypeError, ValueError):
            continue
        remaining = total_raw - filled_raw
        if pct_raw <= 0 or pct_raw >= ODDS_PRECISION or remaining <= 0:
            continue

        taker_space_raw = (remaining * ODDS_PRECISION) // pct_raw - remaining
        taker_usdc = taker_space_raw / USDC_DECIMALS
        if taker_usdc <= 0.0:
            continue

        maker_probability = pct_raw / ODDS_PRECISION
        taker_probability = max(0.0, min(1.0, 1.0 - maker_probability))
        if taker_probability <= 0.0:
            continue
        levels[round(taker_probability, 8)] += taker_usdc
        total += taker_usdc

    ranked = sorted(levels.items(), key=lambda item: item[0])
    if not ranked:
        return {"probability": 0.0, "decimal_odd": 0.0, "available_usdc": 0.0, "top_levels": []}
    best_probability = float(ranked[0][0])
    return {
        "probability": best_probability,
        "decimal_odd": (1.0 / best_probability) if best_probability > 0.0 else 0.0,
        "available_usdc": round(total, 2),
        "top_levels": [
            {"probability": probability, "decimal_odd": 1.0 / probability, "available_usdc": round(size, 2)}
            for probability, size in ranked[:5]
            if probability > 0.0
        ],
    }


def build_live_1x2_board(markets: list[dict[str, Any]], orders: list[dict[str, Any]]) -> list[dict[str, Any]]:
    games: dict[str, dict[str, Any]] = {}
    for market in markets:
        if int(_number(market.get("sportId"))) != SOCCER_ID or int(_number(market.get("type"))) != 1:
            continue
        home = str(market.get("teamOneName") or "").strip()
        away = str(market.get("teamTwoName") or "").strip()
        market_hash = str(market.get("marketHash") or "").strip()
        if not home or not away or not market_hash:
            continue
        event_id = str(market.get("sportXeventId") or "").strip()
        game_time = int(_number(market.get("gameTime")))
        key = event_id or f"{game_time}|{home}|{away}"
        game = games.setdefault(
            key,
            {
                "event_id": event_id or key,
                "name": f"{home} — {away}",
                "home": home,
                "away": away,
                "start": game_time,
                "markets": {},
            },
        )
        outcome_one = str(market.get("outcomeOneName") or "").strip()
        low = outcome_one.casefold()
        if low == home.casefold():
            game["markets"]["P1"] = market_hash
        elif low == "tie":
            game["markets"]["X"] = market_hash
        elif low == away.casefold():
            game["markets"]["P2"] = market_hash

    orders_by_hash: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in orders:
        market_hash = str(row.get("marketHash") or "").strip()
        if market_hash:
            orders_by_hash[market_hash].append(row)

    result: list[dict[str, Any]] = []
    for game in games.values():
        outcomes: dict[str, dict[str, Any]] = {}
        for label in ("P1", "X", "P2"):
            market_hash = str((game.get("markets") or {}).get(label) or "")
            if not market_hash:
                continue
            quote = _taker_quote(orders_by_hash.get(market_hash, []), True)
            quote["market_hash"] = market_hash
            outcomes[label] = quote
        if not outcomes:
            continue
        liquidity = sum(_number(row.get("available_usdc")) for row in outcomes.values())
        result.append(
            {
                "event_id": game["event_id"],
                "name": game["name"],
                "home": game["home"],
                "away": game["away"],
                "start": game["start"],
                "in_running": True,
                "liquidity_usdc": round(liquidity, 2),
                "outcomes": outcomes,
            }
        )
    result.sort(key=lambda row: _number(row.get("liquidity_usdc")), reverse=True)
    return result


def _snapshot_path() -> Path:
    raw = os.getenv("SXBET_BOARD_SNAPSHOT_PATH", "").strip()
    if raw:
        return Path(raw)
    root = Path(os.getenv("RUNTIME_DATA_DIR", "data"))
    return root / "live" / "sxbet_money_snapshot.json"


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

    best_label = ""
    best_score = 0.0
    best: dict[str, Any] = {}
    current_outcomes = current.get("outcomes") or {}
    previous_outcomes = previous.get("outcomes") or {}
    for label in ("P1", "X", "P2"):
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


def fetch_sxbet_state(*, timeout: float | None = None) -> dict[str, Any]:
    started = time.time()
    timeout = float(timeout if timeout is not None else os.getenv("SXBET_PUBLIC_TIMEOUT", "4"))
    path = _snapshot_path()
    previous_state = _load_snapshot(path)
    previous_by_event = {
        str(row.get("event_id") or ""): row
        for row in previous_state.get("events") or []
        if isinstance(row, dict)
    }
    try:
        markets = fetch_live_soccer_markets(timeout=timeout)
        hashes = [
            str(row.get("marketHash") or "").strip()
            for row in markets
            if isinstance(row, dict) and int(_number(row.get("type"))) == 1 and str(row.get("marketHash") or "").strip()
        ]
        orders = fetch_orders(hashes, timeout=timeout)
        events = build_live_1x2_board(markets, orders)
        for event in events:
            event["flow"] = _flow(event, previous_by_event.get(str(event.get("event_id") or "")))
        state = {
            "captured_at": datetime.now(timezone.utc).isoformat(),
            "available": True,
            "source": "sxbet_public_rest",
            "currency": "USDC",
            "semantics": "executable_orderbook_liquidity_not_matched_volume",
            "latency_ms": int((time.time() - started) * 1000),
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
            "source": "sxbet_public_rest",
            "currency": "USDC",
            "error": f"{type(exc).__name__}:{exc}",
            "latency_ms": int((time.time() - started) * 1000),
            "events": [],
        }


__all__ = [
    "BASE_TOKEN",
    "build_live_1x2_board",
    "fetch_live_soccer_markets",
    "fetch_orders",
    "fetch_sxbet_state",
]
