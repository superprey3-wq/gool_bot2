from __future__ import annotations

import os
import urllib.parse
from typing import Any

from .matchbook_exchange import MATCHBOOK_EVENTS_URL, decode_event
from .providers.common import UA


def _page_payload(page: int, per_page: int) -> dict[str, Any]:
    """Fetch one Matchbook page through the shared authenticated client.

    The Matchbook worker replaces ``matchbook_exchange._fetch_events`` with this
    paginated implementation. Calling urllib directly here would silently bypass
    the session-token/auth layer installed at package startup and turn every
    protected API response into ``matchbook_market_unavailable``. Keep all pages
    on the same auth/refresh/error path as the base collector.
    """
    from . import matchbook_auth

    offset = max(0, (int(page) - 1) * int(per_page))
    orderbook_depth = max(3, min(10, int(os.getenv("MATCHBOOK_ORDERBOOK_DEPTH", "5"))))
    params = urllib.parse.urlencode(
        {
            "tag-url-names": "soccer",
            "states": "open,suspended",
            "exchange-type": "back-lay",
            "odds-type": "DECIMAL",
            "include-prices": "true",
            "price-depth": orderbook_depth,
            "price-mode": "expanded",
            "currency": "GBP",
            "minimum-liquidity": 1,
            "include-event-participants": "true",
            "markets-limit": 40,
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
    """Fetch the football board across pages without bypassing Matchbook auth."""
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
        print(
            f"MATCHBOOK_PAGE page={page} offset={(page - 1) * per_page} raw={len(events)} "
            f"added={added} total={len(rows)} auth=shared",
            flush=True,
        )
        if len(events) < per_page or added == 0:
            break

    return rows
