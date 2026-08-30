from __future__ import annotations

import time
from urllib.parse import urlencode

from .common import ProviderMatch, http_json, pair_score

BASE = "https://webws.365scores.com/web"


class Scores365Provider:
    name = "365scores"

    def __init__(self) -> None:
        self._live_cache: tuple[float, list[dict]] = (0.0, [])
        self._detail_cache: dict[str, tuple[float, dict]] = {}

    def _get(self, path: str, params: dict) -> tuple[int, dict]:
        return http_json(
            BASE + "/" + path.lstrip("/") + "?" + urlencode(params),
            headers={"User-Agent": "Mozilla/5.0", "Accept": "*/*", "Referer": "https://www.365scores.com/"},
            timeout=9,
        )

    def live_rows(self) -> list[dict]:
        now = time.time()
        if now - self._live_cache[0] < 180:
            return self._live_cache[1]
        code, data = self._get("games/", {"appTypeId": 5, "langId": 1, "timezoneName": "Etc/UTC", "sports": 1})
        rows: list[dict] = []
        if code == 200 and isinstance(data, dict):
            rows = [g for g in (data.get("games") or []) if isinstance(g, dict) and g.get("statusGroup") == 3]
        self._live_cache = (now, rows)
        return rows

    def find_match(self, home: str, away: str) -> tuple[dict | None, float]:
        best: dict | None = None
        best_score = 0.0
        for game in self.live_rows():
            h = (game.get("homeCompetitor") or {}).get("name")
            a = (game.get("awayCompetitor") or {}).get("name")
            score = pair_score(home, away, str(h or ""), str(a or ""))
            if score > best_score:
                best, best_score = game, score
        return (best, best_score) if best_score >= 0.72 else (None, best_score)

    def _detail(self, game_id: str) -> dict:
        now = time.time()
        cached = self._detail_cache.get(str(game_id))
        if cached and now - cached[0] < 150:
            return cached[1]
        code, data = self._get(
            "game/",
            {"appTypeId": 5, "langId": 1, "timezoneName": "Etc/UTC", "gameId": game_id, "topBookmaker": 14},
        )
        game = (data.get("game") or {}) if code == 200 and isinstance(data, dict) else {}
        self._detail_cache[str(game_id)] = (now, game)
        return game

    def enrich(self, home: str, away: str) -> ProviderMatch | None:
        hit, score = self.find_match(home, away)
        if not hit:
            return None
        game_id = hit.get("id")
        if not game_id:
            return None
        game = self._detail(str(game_id))
        shots = (((game.get("chartEvents") or {}).get("events") or []) if isinstance(game, dict) else [])
        home_id = (hit.get("homeCompetitor") or {}).get("id")
        xg = [0.0, 0.0]
        xgot = [0.0, 0.0]
        shot_counts = [0.0, 0.0]
        has_xg = has_xgot = False
        for shot in shots:
            if not isinstance(shot, dict):
                continue
            side = 0 if shot.get("competitorId") == home_id else 1
            shot_counts[side] += 1
            try:
                if shot.get("xg") not in (None, "", "-"):
                    xg[side] += float(shot["xg"]); has_xg = True
            except Exception:
                pass
            try:
                if shot.get("xgot") not in (None, "", "-"):
                    xgot[side] += float(shot["xgot"]); has_xgot = True
            except Exception:
                pass
        events = game.get("events") or [] if isinstance(game, dict) else []
        reds = [e for e in events if isinstance(e, dict) and ((e.get("eventType") or {}).get("id") == 3)]
        stats: dict[str, tuple[float, float]] = {"shotmap_shots": (shot_counts[0], shot_counts[1])}
        if has_xg:
            stats["xg"] = (round(xg[0], 3), round(xg[1], 3))
        if has_xgot:
            stats["xgot"] = (round(xgot[0], 3), round(xgot[1], 3))
        return ProviderMatch(
            provider=self.name,
            provider_match_id=str(game_id),
            home=home,
            away=away,
            stats=stats,
            meta={
                "match_score": round(score, 3),
                "red_cards": len(reds),
                "has_shotmap": bool(shots),
                "has_stats": bool(game.get("hasStats")),
                "has_lineups": bool(game.get("hasLineups")),
            },
        )
