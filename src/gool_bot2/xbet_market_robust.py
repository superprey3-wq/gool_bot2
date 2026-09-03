from __future__ import annotations

import json
import os
import time
import urllib.parse
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from typing import Any

from . import xbet_market_pressure as market


def _half_goal_only(rows: list[dict[str, Any]] | None) -> list[dict[str, Any]]:
    """Keep classic O/U x.5 lines only (0.5, 1.5, 2.5, ...).

    Integer Asian totals and quarter lines are deliberately excluded from the
    GOOL Multi market state, so the router cannot accidentally select a push or
    split-stake Asian line.
    """
    out: list[dict[str, Any]] = []
    for row in rows or []:
        try:
            line = float(row.get("line"))
        except (TypeError, ValueError):
            continue
        if abs((line % 1.0) - 0.5) < 1e-9:
            out.append(dict(row))
    return out


def decode_standard_markets(game: dict[str, Any]) -> dict[str, Any]:
    """Decode full-match markets plus the real 1xBet 1st-half subgame total."""
    decoded = market.decode_markets(game)
    decoded["match_total"] = _half_goal_only(decoded.get("match_total"))
    decoded["home_total"] = _half_goal_only(decoded.get("home_total"))
    decoded["away_total"] = _half_goal_only(decoded.get("away_total"))

    first_half: list[dict[str, Any]] = []
    for subgame in game.get("SG") or []:
        if not isinstance(subgame, dict):
            continue
        period = subgame.get("P")
        name = str(subgame.get("PN") or "").strip().lower()
        if period != 1 and name not in {"1st half", "first half", "1 half"}:
            continue
        # Running _nodes on the SG object itself intentionally makes these nodes
        # non-sub for _pairs(), while still limiting decoding to this exact period.
        nodes = market._nodes(subgame)
        first_half = _half_goal_only(market._pairs(nodes, 9, 10, 4))
        if first_half:
            break
    decoded["first_half_total"] = first_half
    return decoded


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

    @staticmethod
    def _flat(markets: dict[str, Any]) -> dict[str, dict[str, Any]]:
        out = market.XBetMarketCollector._flat(markets)
        for row in markets.get("first_half_total") or []:
            if not row.get("over"):
                continue
            over = float(row["over"])
            under = None if row.get("under") is None else float(row["under"])
            out[f"first_half_total:{row.get('line')}"] = {
                "odd": over,
                "prob": market._norm_probability(over, under),
            }
        return out

    def collect_once(self) -> dict[str, Any]:
        started = time.time()
        matches = self.flashscore.live_matches()
        root, candidates = self._fetch_index()
        output: dict[str, Any] = {}
        jobs = []
        with ThreadPoolExecutor(max_workers=max(2, int(os.getenv("XBET_GAME_WORKERS", "8")))) as pool:
            for fs in matches:
                event_id = self._map(fs, candidates)
                if event_id:
                    jobs.append((fs, event_id, pool.submit(self._game, event_id)))
            for fs, event_id, future in jobs:
                try:
                    game = future.result(timeout=12)
                except Exception:
                    game = None
                if not game:
                    continue
                markets = decode_standard_markets(game)
                now = time.time()
                score = (int(fs.home_score or 0), int(fs.away_score or 0))
                pressure = self._pressure(str(fs.provider_match_id), score, now, markets)
                output[str(fs.provider_match_id)] = {
                    "flashscore_event_id": str(fs.provider_match_id),
                    "xbet_event_id": event_id,
                    "home": fs.home,
                    "away": fs.away,
                    "minute": int(fs.minute or 0),
                    "score_home": score[0],
                    "score_away": score[1],
                    "captured_at": datetime.now(timezone.utc).isoformat(),
                    "markets": markets,
                    "pressure": pressure,
                    "line_move": False,
                }
        state = {
            "captured_at": datetime.now(timezone.utc).isoformat(),
            "root": root,
            "latency_ms": int((time.time() - started) * 1000),
            "matches": output,
        }
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.state_path.with_suffix(".tmp")
        tmp.write_text(json.dumps(state, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
        tmp.replace(self.state_path)
        self.history_path.parent.mkdir(parents=True, exist_ok=True)
        with self.history_path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(state, ensure_ascii=False, separators=(",", ":")) + "\n")
        return state
