from __future__ import annotations

import os
import time
import urllib.parse
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any

from . import xbet_market_pressure as market


class RobustXBetMarketCollector(market.XBetMarketCollector):
    """1xBet collector resilient to partial mirrors and short index blackouts.

    Public 1xBet LiveFeed mirrors can return different subsets of the football
    live index, or briefly return an empty index from every mirror. We merge all
    reachable roots/queries and keep a deliberately short last-good index cache.
    The cache only preserves candidate ids/names; every actual market snapshot is
    still fetched live through GetGameZip, so stale odds are never replayed as a
    new market observation.
    """

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self._event_roots: dict[str, list[str]] = {}
        self._index_root_counts: dict[str, int] = {}
        self._last_index_rows: list[dict[str, Any]] = []
        self._last_event_roots: dict[str, list[str]] = {}
        self._last_index_at: float = 0.0
        self._index_cache_used: bool = False
        self._index_cache_age_seconds: float | None = None

    @staticmethod
    def _index_rows(root: str, query: str) -> tuple[str, list[dict[str, Any]]]:
        payload = market._http_json(f"{root}/Get1x2_VZip?{query}", timeout=7.0)
        value = payload.get("Value") if isinstance(payload, dict) else None
        if not isinstance(value, list):
            return root, []
        rows: list[dict[str, Any]] = []
        for event in value:
            if not isinstance(event, dict):
                continue
            event_id = str(event.get("I") or "").strip()
            home = str(event.get("O1") or "").strip()
            away = str(event.get("O2") or "").strip()
            if event_id and home and away:
                rows.append({"event_id": event_id, "home": home, "away": away, "root": root})
        return root, rows

    def _fetch_index(self) -> tuple[str | None, list[dict[str, Any]]]:
        roots = [self.active_root] + [root for root in market.ROOTS if root != self.active_root]
        jobs = []
        merged: dict[str, dict[str, Any]] = {}
        event_roots: dict[str, list[str]] = {}
        root_events: dict[str, set[str]] = {root: set() for root in roots}

        with ThreadPoolExecutor(max_workers=max(2, min(8, len(roots) * len(market.INDEX_QUERIES)))) as pool:
            for root in roots:
                for query in market.INDEX_QUERIES:
                    jobs.append(pool.submit(self._index_rows, root, query))
            for future in as_completed(jobs):
                try:
                    root, rows = future.result()
                except Exception:
                    continue
                for row in rows:
                    event_id = str(row["event_id"])
                    root_events.setdefault(root, set()).add(event_id)
                    roots_for_event = event_roots.setdefault(event_id, [])
                    if root not in roots_for_event:
                        roots_for_event.append(root)
                    previous = merged.get(event_id)
                    if previous is None or (not previous.get("home") and row.get("home")):
                        merged[event_id] = row

        self._index_root_counts = {root: len(ids) for root, ids in root_events.items()}
        now = time.monotonic()
        if merged:
            rows = list(merged.values())
            self._event_roots = event_roots
            self._last_event_roots = {key: list(value) for key, value in event_roots.items()}
            self._last_index_rows = [dict(row) for row in rows]
            self._last_index_at = now
            self._index_cache_used = False
            self._index_cache_age_seconds = 0.0
            best_root = max(roots, key=lambda root: self._index_root_counts.get(root, 0))
            if self._index_root_counts.get(best_root, 0) > 0:
                self.active_root = best_root
            return self.active_root, rows

        max_cache_age = max(0.0, float(os.getenv("XBET_INDEX_CACHE_SECONDS", "45")))
        age = now - self._last_index_at if self._last_index_at > 0 else 999999.0
        if self._last_index_rows and age <= max_cache_age:
            self._event_roots = {key: list(value) for key, value in self._last_event_roots.items()}
            self._index_cache_used = True
            self._index_cache_age_seconds = round(age, 1)
            return self.active_root, [dict(row) for row in self._last_index_rows]

        self._event_roots = {}
        self._index_cache_used = False
        self._index_cache_age_seconds = None
        return None, []

    def _game(self, event_id: str) -> dict[str, Any] | None:
        params = {
            "id": event_id,
            "lng": "en",
            "cfview": 0,
            "isSubGames": "true",
            "GroupEvents": "true",
            "allEventsGroupSubGames": "true",
            "countevents": 250,
            "grMode": 2,
        }
        roots: list[str] = []
        for root in [*(self._event_roots.get(str(event_id)) or []), self.active_root, *market.ROOTS]:
            if root and root not in roots:
                roots.append(root)
        for root in roots:
            payload = market._http_json(f"{root}/GetGameZip?{urllib.parse.urlencode(params)}", timeout=7.0)
            value = payload.get("Value") if isinstance(payload, dict) else None
            if isinstance(value, dict):
                self.active_root = root
                return value
        return None
