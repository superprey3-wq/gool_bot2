from __future__ import annotations

import urllib.parse
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any

from . import xbet_market_pressure as market


class RobustXBetMarketCollector(market.XBetMarketCollector):
    """1xBet collector that does not trust a single intermittently partial root.

    The public 1xBet LiveFeed mirrors can return different subsets of the same
    football live index from one request to the next. The legacy collector used
    the first non-empty response, which could make an already mapped Flashscore
    match disappear on the next 12-second cycle. This collector merges candidate
    events from every reachable configured root/query and remembers which roots
    advertised each event so GetGameZip is attempted against the most likely
    mirrors first.
    """

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self._event_roots: dict[str, list[str]] = {}
        self._index_root_counts: dict[str, int] = {}

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

        self._event_roots = event_roots
        self._index_root_counts = {root: len(ids) for root, ids in root_events.items()}
        if not merged:
            return None, []

        best_root = max(roots, key=lambda root: self._index_root_counts.get(root, 0))
        if self._index_root_counts.get(best_root, 0) > 0:
            self.active_root = best_root
        return self.active_root, list(merged.values())

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
