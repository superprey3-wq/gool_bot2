from __future__ import annotations

import os
import time
from typing import Any

from .common import pair_score
from .scores365 import Scores365Provider


HISTORY_TTL_SECONDS = 6 * 60 * 60


def _number(value: Any) -> int | None:
    try:
        if value in (None, "", "-"):
            return None
        number = float(value)
        if number < 0:
            return None
        return int(number)
    except (TypeError, ValueError):
        return None


def _halftime_score(game: dict[str, Any]) -> tuple[int, int] | None:
    for stage in game.get("stages") or []:
        if not isinstance(stage, dict):
            continue
        sid = stage.get("id")
        name = " ".join(str(stage.get(key) or "") for key in ("name", "shortName")).casefold()
        if sid != 7 and "halftime" not in name and name.strip() != "ht":
            continue
        home = _number(stage.get("homeCompetitorScore"))
        away = _number(stage.get("awayCompetitorScore"))
        if home is not None and away is not None:
            return home, away
    return None


def _final_score(game: dict[str, Any]) -> tuple[int, int] | None:
    home = _number((game.get("homeCompetitor") or {}).get("score"))
    away = _number((game.get("awayCompetitor") or {}).get("score"))
    if home is not None and away is not None:
        return home, away
    for stage in game.get("stages") or []:
        if not isinstance(stage, dict):
            continue
        sid = stage.get("id")
        name = " ".join(str(stage.get(key) or "") for key in ("name", "shortName")).casefold()
        if sid != 9 and "90" not in name and "full" not in name:
            continue
        home = _number(stage.get("homeCompetitorScore"))
        away = _number(stage.get("awayCompetitorScore"))
        if home is not None and away is not None:
            return home, away
    return None


def _finished(game: dict[str, Any]) -> bool:
    try:
        if int(game.get("statusGroup") or 0) == 4:
            return True
    except (TypeError, ValueError):
        pass
    if bool(game.get("justEnded")):
        return True
    text = " ".join(str(game.get(key) or "") for key in ("statusText", "shortStatusText", "winDescription")).casefold()
    if any(token in text for token in ("finished", "full time", "full-time", "after penalties", "aet")):
        return True
    for stage in game.get("stages") or []:
        if not isinstance(stage, dict) or not bool(stage.get("isEnded")):
            continue
        if stage.get("id") == 9:
            return True
        name = " ".join(str(stage.get(key) or "") for key in ("name", "shortName")).casefold()
        if "90" in name or "full" in name:
            return True
    return False


def _history_row(game: dict[str, Any]) -> dict[str, Any] | None:
    if not game or not _finished(game):
        return None
    home_node = game.get("homeCompetitor") or {}
    away_node = game.get("awayCompetitor") or {}
    home = str(home_node.get("name") or "").strip()
    away = str(away_node.get("name") or "").strip()
    final = _final_score(game)
    halftime = _halftime_score(game)
    if not home or not away or final is None or halftime is None:
        return None
    if halftime[0] > final[0] or halftime[1] > final[1]:
        return None
    return {
        "event_id": str(game.get("id") or ""),
        "home": home,
        "away": away,
        "home_score": final[0],
        "away_score": final[1],
        "halftime_home_score": halftime[0],
        "halftime_away_score": halftime[1],
        "second_half_home_score": final[0] - halftime[0],
        "second_half_away_score": final[1] - halftime[1],
        "timestamp": str(game.get("startTime") or ""),
        "source": "365scores_recent_halves",
    }


def _similar(name: str, target: str) -> bool:
    if not name or not target:
        return False
    return pair_score(target, target, name, name) >= 0.72


def _has_team(row: dict[str, Any], team: str) -> bool:
    return _similar(str(row.get("home") or ""), team) or _similar(str(row.get("away") or ""), team)


def _history_detail(provider: Scores365Provider, game_id: Any) -> dict[str, Any]:
    if not hasattr(provider, "_half_history_detail_cache"):
        provider._half_history_detail_cache = {}
    cache = provider._half_history_detail_cache
    key = str(game_id)
    now = time.time()
    cached = cache.get(key)
    if cached and now - cached[0] < HISTORY_TTL_SECONDS:
        return cached[1]
    code, data = provider._get("game/", {
        "appTypeId": 5,
        "langId": 1,
        "timezoneName": "Etc/UTC",
        "gameId": key,
        "topBookmaker": 14,
    })
    game = (data.get("game") or {}) if code == 200 and isinstance(data, dict) else {}
    cache[key] = (now, game)
    return game


def _recent_rows(provider: Scores365Provider, ids: list[Any], limit: int) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    for game_id in ids:
        if len(out) >= limit:
            break
        key = str(game_id or "")
        if not key or key in seen:
            continue
        seen.add(key)
        row = _history_row(_history_detail(provider, key))
        if row is not None:
            out.append(row)
    return out


def _merge_rows(primary: list[dict[str, Any]], fallback: list[dict[str, Any]], limit: int) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str, int, int]] = set()
    for row in [*primary, *fallback]:
        if not isinstance(row, dict):
            continue
        ident = (
            str(row.get("home") or "").casefold(),
            str(row.get("away") or "").casefold(),
            str(row.get("timestamp") or "")[:10],
            int(row.get("home_score") or 0),
            int(row.get("away_score") or 0),
        )
        if ident in seen:
            continue
        seen.add(ident)
        out.append(dict(row))
        if len(out) >= limit:
            break
    return out


def install() -> None:
    if getattr(Scores365Provider, "_prematch_half_guard_installed", False):
        return

    original_context = Scores365Provider.prematch_context
    original_enrich = Scores365Provider.enrich

    def prematch_context(self: Scores365Provider, home: str, away: str, limit: int = 10) -> dict[str, Any]:
        base = original_context(self, home, away, limit)
        hit, score = self.find_match(home, away)
        if not isinstance(hit, dict) or not hit.get("id"):
            return base
        detail = self._detail(str(hit.get("id")))
        home_node = detail.get("homeCompetitor") or hit.get("homeCompetitor") or {}
        away_node = detail.get("awayCompetitor") or hit.get("awayCompetitor") or {}
        half_limit = max(3, min(limit, int(os.getenv("SCORES365_HALF_HISTORY_MATCHES", "6"))))
        home_rows = _recent_rows(self, list(home_node.get("recentMatches") or []), half_limit)
        away_rows = _recent_rows(self, list(away_node.get("recentMatches") or []), half_limit)
        if not home_rows and not away_rows:
            base["has_trends"] = bool(detail.get("hasTrends"))
            base["has_top_trends"] = bool(detail.get("hasTopTrends"))
            return base

        base_home = [dict(row) for row in (base.get("home_recent") or []) if isinstance(row, dict)]
        base_away = [dict(row) for row in (base.get("away_recent") or []) if isinstance(row, dict)]
        merged_home = _merge_rows(home_rows, base_home, limit)
        merged_away = _merge_rows(away_rows, base_away, limit)
        all_rows = _merge_rows(home_rows + away_rows, [], limit * 2)
        h2h_half = [row for row in all_rows if _has_team(row, home) and _has_team(row, away)]
        base_h2h = [dict(row) for row in (base.get("h2h") or []) if isinstance(row, dict)]

        return {
            **base,
            "source": "365scores_recent_halves",
            "match_score": round(float(score or 0.0), 3),
            "home_recent": merged_home,
            "away_recent": merged_away,
            "home_at_home": [row for row in merged_home if _similar(str(row.get("home") or ""), home)][:limit],
            "away_away": [row for row in merged_away if _similar(str(row.get("away") or ""), away)][:limit],
            "h2h": _merge_rows(h2h_half, base_h2h, limit),
            "half_score_matches": len(home_rows) + len(away_rows),
            "has_trends": bool(detail.get("hasTrends")),
            "has_top_trends": bool(detail.get("hasTopTrends")),
            "has_previous_meetings": bool(detail.get("hasPreviousMeetings")),
            "has_recent_matches": bool(detail.get("hasRecentMatches")),
        }

    def enrich(self: Scores365Provider, home: str, away: str):
        result = original_enrich(self, home, away)
        if result is None:
            return None
        game = self._detail(str(result.provider_match_id))
        halftime = _halftime_score(game)
        meta = dict(result.meta or {})
        meta.update({
            "halftime_score": None if halftime is None else list(halftime),
            "has_trends": bool(game.get("hasTrends")),
            "has_top_trends": bool(game.get("hasTopTrends")),
            "has_previous_meetings": bool(game.get("hasPreviousMeetings")),
            "has_recent_matches": bool(game.get("hasRecentMatches")),
        })
        return type(result)(**{**result.__dict__, "meta": meta})

    Scores365Provider.prematch_context = prematch_context
    Scores365Provider.enrich = enrich
    Scores365Provider._prematch_half_guard_installed = True


__all__ = ["install", "_halftime_score", "_history_row"]
