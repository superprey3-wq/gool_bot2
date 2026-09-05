from __future__ import annotations

from typing import Any

from .fotmob import FotMobProvider


_ORIGINAL_ENRICH = FotMobProvider.enrich
_INSTALLED = False


def _num(value: Any) -> float | None:
    try:
        if value in (None, "", "-"):
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _player_nodes(value: Any) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    seen: set[str] = set()

    def walk(node: Any) -> None:
        if isinstance(node, dict):
            nested = node.get("player")
            if isinstance(nested, dict):
                walk(nested)
            player_id = node.get("id") or node.get("playerId")
            name = node.get("name") or node.get("playerName")
            if player_id is not None and name:
                key = str(player_id)
                if key not in seen:
                    seen.add(key)
                    out.append(node)
                    return
            for child in node.values():
                if isinstance(child, (dict, list)):
                    walk(child)
        elif isinstance(node, list):
            for child in node:
                walk(child)

    walk(value)
    return out


def _team_summary(team: Any) -> dict[str, Any]:
    if not isinstance(team, dict):
        return {"available": False}
    starters = _player_nodes(team.get("starters") or [])
    subs = _player_nodes(team.get("subs") or [])
    unavailable = _player_nodes(team.get("unavailable") or [])
    starter_ratings = [
        value for row in starters
        for value in [_num(row.get("rating") or row.get("performanceRating"))]
        if value is not None
    ]
    return {
        "available": bool(starters or team.get("formation") or team.get("rating") is not None),
        "team_id": team.get("id"),
        "name": team.get("name"),
        "formation": team.get("formation"),
        "team_rating": _num(team.get("rating")),
        "starters": len(starters),
        "subs": len(subs),
        "unavailable": len(unavailable),
        "average_starter_age": _num(team.get("averageStarterAge")),
        "starter_market_value": _num(team.get("totalStarterMarketValue")),
        "average_starter_rating": None if not starter_ratings else round(sum(starter_ratings) / len(starter_ratings), 3),
    }


def lineup_summary(detail: dict[str, Any]) -> dict[str, Any]:
    lineup = ((detail.get("content") or {}).get("lineup") or {}) if isinstance(detail, dict) else {}
    if not isinstance(lineup, dict):
        return {"available": False}
    home = _team_summary(lineup.get("homeTeam"))
    away = _team_summary(lineup.get("awayTeam"))
    return {
        "available": bool(home.get("available") or away.get("available")),
        "lineup_type": lineup.get("lineupType"),
        "source": lineup.get("source"),
        "home": home,
        "away": away,
        "total_unavailable": int(home.get("unavailable") or 0) + int(away.get("unavailable") or 0),
        "total_starters": int(home.get("starters") or 0) + int(away.get("starters") or 0),
    }


def _enrich_with_lineup(self: FotMobProvider, home: str, away: str):
    result = _ORIGINAL_ENRICH(self, home, away)
    if result is None:
        return None
    try:
        detail = self._detail(str(result.provider_match_id))
        summary = lineup_summary(detail)
    except Exception:
        summary = {"available": False}
    meta = dict(result.meta or {})
    meta["lineup_summary"] = summary
    meta["has_lineup"] = bool(summary.get("available") or meta.get("has_lineup"))
    result.meta = meta
    return result


def install() -> None:
    global _INSTALLED
    if _INSTALLED:
        return
    FotMobProvider.enrich = _enrich_with_lineup
    _INSTALLED = True


__all__ = ["install", "lineup_summary"]
