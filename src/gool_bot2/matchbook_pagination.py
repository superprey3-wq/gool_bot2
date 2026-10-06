from __future__ import annotations

import os
import time
import urllib.parse
from typing import Any

from .matchbook_exchange import MATCHBOOK_EVENTS_URL, decode_event
from .providers.common import UA

MATCHBOOK_SPORTS_URL = "https://api.matchbook.com/edge/rest/lookups/sports"

# Resolve IDs dynamically instead of freezing Matchbook's navigation IDs in code.
# The lookup is cached because sports metadata changes far less often than prices.
_SPORT_CACHE: dict[str, Any] = {"ts": 0.0, "mapping": {}}
_WANTED_SPORTS = {
    "soccer": "football",
    "football": "football",
    "basketball": "basketball",
    "ice hockey": "hockey",
    "hockey": "hockey",
}


def _number(value: Any) -> int | None:
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return None


def _sport_rows(payload: dict[str, Any]) -> list[dict[str, Any]]:
    for key in ("sports", "data", "items"):
        rows = payload.get(key)
        if isinstance(rows, list):
            return [dict(row) for row in rows if isinstance(row, dict)]
    return []


def resolve_sport_ids(*, force: bool = False) -> dict[int, str]:
    """Return active Matchbook sport-id -> GOOL sport key.

    We intentionally ask Matchbook's navigation endpoint at runtime because the
    Events API accepts sport-ids and the lookup endpoint is the authoritative
    source of current IDs. If the lookup is unavailable we fall back to the old
    soccer tag so football collection keeps working.
    """
    now = time.monotonic()
    ttl = max(300.0, float(os.getenv("MATCHBOOK_SPORT_LOOKUP_TTL_SECONDS", str(6 * 3600))))
    cached = dict(_SPORT_CACHE.get("mapping") or {})
    if cached and not force and now - float(_SPORT_CACHE.get("ts") or 0.0) <= ttl:
        return {int(k): str(v) for k, v in cached.items()}

    from . import matchbook_auth

    params = urllib.parse.urlencode(
        {
            "offset": 0,
            "per-page": 200,
            "order": "name asc",
            "status": "active",
        }
    )
    try:
        payload = matchbook_auth._request_json(f"{MATCHBOOK_SPORTS_URL}?{params}", UA)
    except Exception as exc:
        print(f"MATCHBOOK_SPORT_LOOKUP error={type(exc).__name__}:{exc}", flush=True)
        return cached

    mapping: dict[int, str] = {}
    for row in _sport_rows(payload if isinstance(payload, dict) else {}):
        sport_id = _number(row.get("id") if row.get("id") is not None else row.get("sport-id"))
        name = str(row.get("name") or row.get("sport-name") or "").strip().casefold()
        if sport_id is None or not name:
            continue
        gool_key = _WANTED_SPORTS.get(name)
        if gool_key:
            mapping[sport_id] = gool_key
    if mapping:
        _SPORT_CACHE.update({"ts": now, "mapping": dict(mapping)})
        print(
            "MATCHBOOK_SPORT_LOOKUP "
            + " ".join(f"{key}={sport_id}" for sport_id, key in sorted(mapping.items(), key=lambda x: x[1])),
            flush=True,
        )
        return mapping
    return cached


def _sport_filter_params() -> dict[str, str]:
    mapping = resolve_sport_ids()
    if mapping:
        return {"sport-ids": ",".join(str(sport_id) for sport_id in sorted(mapping))}
    # Backward-compatible safety net: if navigation lookup fails, never lose the
    # existing football board.
    return {"tag-url-names": "soccer"}


def _page_payload(page: int, per_page: int) -> dict[str, Any]:
    """Fetch one Matchbook page through the shared authenticated client."""
    from . import matchbook_auth

    offset = max(0, (int(page) - 1) * int(per_page))
    orderbook_depth = max(3, min(10, int(os.getenv("MATCHBOOK_ORDERBOOK_DEPTH", "5"))))
    params = urllib.parse.urlencode(
        {
            **_sport_filter_params(),
            "states": "open,suspended",
            "exchange-type": "back-lay",
            "odds-type": "DECIMAL",
            "include-prices": "true",
            "price-depth": orderbook_depth,
            "price-mode": "expanded",
            "currency": "GBP",
            "minimum-liquidity": 1,
            "include-event-participants": "true",
            "markets-limit": max(40, min(120, int(os.getenv("MATCHBOOK_MARKETS_LIMIT", "80")))),
            "per-page": per_page,
            "offset": offset,
        }
    )
    payload = matchbook_auth._request_json(
        f"{MATCHBOOK_EVENTS_URL}?{params}",
        UA,
    )
    return payload if isinstance(payload, dict) else {}


def fetch_events_paginated() -> list[dict[str, Any]]:
    """Fetch football + basketball + ice-hockey boards with shared auth."""
    per_page = max(20, min(100, int(os.getenv("MATCHBOOK_EVENTS_PER_PAGE", "100"))))
    max_pages = max(1, min(12, int(os.getenv("MATCHBOOK_MAX_PAGES", "6"))))
    seen: set[str] = set()
    rows: list[dict[str, Any]] = []

    for page in range(1, max_pages + 1):
        payload = _page_payload(page, per_page)
        events = [row for row in (payload.get("events") or []) if isinstance(row, dict)]
        if not events:
            break
        added = 0
        for event in events:
            decoded = decode_event(event)
            if decoded is None:
                continue
            event_id = str(decoded.get("event_id") or "")
            identity = event_id or f"{decoded.get('home')}|{decoded.get('away')}|{decoded.get('start')}"
            if identity in seen:
                continue
            seen.add(identity)
            rows.append(decoded)
            added += 1
        by_sport: dict[str, int] = {}
        for row in rows:
            key = str(row.get("sport_key") or "unknown")
            by_sport[key] = by_sport.get(key, 0) + 1
        print(
            f"MATCHBOOK_PAGE page={page} offset={(page - 1) * per_page} raw={len(events)} "
            f"added={added} total={len(rows)} sports={by_sport} auth=shared",
            flush=True,
        )
        if len(events) < per_page or added == 0:
            break

    return rows
