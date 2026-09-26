from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from .common import pair_score
from .fotmob import FotMobProvider
from .scores365 import Scores365Provider


_BUCKETS = ("home_recent", "away_recent", "home_at_home", "away_away", "h2h")


def _identity(row: dict[str, Any]) -> tuple:
    return (
        str(row.get("home") or "").casefold().strip(),
        str(row.get("away") or "").casefold().strip(),
        str(row.get("timestamp") or "")[:10],
        row.get("home_score"),
        row.get("away_score"),
    )


def merge_contexts(*contexts: dict[str, Any], limit: int = 10) -> dict[str, Any]:
    out: dict[str, Any] = {"sources": []}
    for bucket in _BUCKETS:
        rows = []
        seen = set()
        for ctx in contexts:
            for row in ctx.get(bucket) or []:
                if not isinstance(row, dict):
                    continue
                key = _identity(row)
                if key in seen:
                    continue
                seen.add(key)
                rows.append(dict(row))
        out[bucket] = rows[:limit]
    for ctx in contexts:
        source = str(ctx.get("source") or "").strip()
        if source:
            out["sources"].append(source)
        out["sources"].extend(ctx.get("sources") or [])
    out["sources"] = list(dict.fromkeys(out["sources"]))
    return out


class PrematchDataFusion:
    """Enrich a Flashscore fixture before the price stage.

    Flashscore remains the schedule authority. Existing FotMob and 365Scores
    providers only contribute football history/context; bookmaker prices are
    deliberately not touched here.
    """

    def __init__(self, flashscore) -> None:
        self.flashscore = flashscore
        self.fotmob = FotMobProvider()
        self.scores365 = Scores365Provider()
        self._365_day_cache: dict[str, list[dict[str, Any]]] = {}

    def _fotmob(self, match, limit: int) -> dict[str, Any]:
        ts = float((match.meta or {}).get("scheduled_start_ts") or 0)
        day = datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y%m%d") if ts else None
        hit, score = self.fotmob.find_match(match.home, match.away, date_key=day)
        if not hit:
            return {"source": "fotmob", "match_score": score}
        event_id = hit.get("id") or hit.get("matchId")
        if not event_id:
            return {"source": "fotmob", "match_score": score}
        from .fotmob import _embedded_history
        ctx = _embedded_history(self.fotmob._detail(str(event_id)), match.home, match.away, limit)
        ctx["match_score"] = round(float(score or 0), 3)
        return ctx

    def _365_rows(self, day: str) -> list[dict[str, Any]]:
        if day in self._365_day_cache:
            return self._365_day_cache[day]
        params = {
            "appTypeId": 5, "langId": 1, "timezoneName": "Etc/UTC",
            "sports": 1, "startDate": day, "endDate": day,
        }
        rows: list[dict[str, Any]] = []
        for path in ("games/fixtures/", "games/"):
            try:
                code, data = self.scores365._get(path, params)
            except Exception:
                continue
            if code == 200 and isinstance(data, dict):
                rows = [g for g in (data.get("games") or []) if isinstance(g, dict)]
                if rows:
                    break
        self._365_day_cache[day] = rows
        return rows

    def _scores365(self, match, limit: int) -> dict[str, Any]:
        ts = float((match.meta or {}).get("scheduled_start_ts") or 0)
        if not ts:
            return {"source": "365scores"}
        day = datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%d")
        best = None
        best_score = 0.0
        for game in self._365_rows(day):
            home = (game.get("homeCompetitor") or {}).get("name")
            away = (game.get("awayCompetitor") or {}).get("name")
            score = pair_score(match.home, match.away, str(home or ""), str(away or ""))
            if score > best_score:
                best, best_score = game, score
        if best is None or best_score < 0.72 or not best.get("id"):
            return {"source": "365scores", "match_score": best_score}
        from .scores365 import _embedded_history
        ctx = _embedded_history(self.scores365._detail(str(best["id"])), match.home, match.away, limit)
        ctx["match_score"] = round(best_score, 3)
        return ctx

    def context(self, match, limit: int = 10) -> dict[str, Any]:
        try:
            fs = self.flashscore.fetch_match_history(match.provider_match_id, match.home, match.away, limit=limit) or {}
        except Exception:
            fs = {}
        fs["source"] = "flashscore_h2h"
        try:
            fm = self._fotmob(match, limit)
        except Exception:
            fm = {"source": "fotmob"}
        try:
            s365 = self._scores365(match, limit)
        except Exception:
            s365 = {"source": "365scores"}
        return merge_contexts(fs, fm, s365, limit=limit)
