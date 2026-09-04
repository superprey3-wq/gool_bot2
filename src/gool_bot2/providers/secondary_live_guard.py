from __future__ import annotations

import time
from datetime import datetime, timezone
from typing import Any
from urllib.parse import urlencode

from .common import ProviderMatch, http_json
from .fotmob import BASES as FOTMOB_BASES, FotMobProvider
from .scores365 import BASE as SCORES365_BASE, Scores365Provider


FOTMOB_DETAIL_TTL_SECONDS = 20.0
SCORES365_DETAIL_TTL_SECONDS = 20.0
SCORES365_STATS_DEFAULT_TTL_SECONDS = 10.0


def _iso_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _number(value: Any) -> float | None:
    try:
        if value in (None, "", "-"):
            return None
        return float(str(value).strip().replace("%", ""))
    except (TypeError, ValueError):
        return None


def _minute(value: Any, added: Any = None) -> tuple[int, str]:
    base = int(_number(value) or 0)
    extra = int(_number(added) or 0)
    total = base + extra
    return total, f"{base}+{extra}" if extra else str(base)


def parse_fotmob_goal_timeline(detail: dict[str, Any]) -> list[dict[str, Any]]:
    events = (((detail.get("content") or {}).get("matchFacts") or {}).get("events") or {}).get("events") or []
    goals: list[dict[str, Any]] = []
    for event in events:
        if not isinstance(event, dict) or str(event.get("type") or "").casefold() != "goal":
            continue
        minute, display = _minute(event.get("time"), event.get("overloadTime"))
        if minute <= 0:
            continue
        score = event.get("newScore")
        if not isinstance(score, (list, tuple)) or len(score) < 2:
            home_score = event.get("homeScore")
            away_score = event.get("awayScore")
            if home_score is None or away_score is None:
                continue
            home_score = int(_number(home_score) or 0)
            away_score = int(_number(away_score) or 0)
            if bool(event.get("isHome")):
                home_score += 1
            else:
                away_score += 1
            score = [home_score, away_score]
        goals.append({
            "minute": minute,
            "display_minute": display,
            "period": "1H" if int(_number(event.get("time")) or 0) <= 45 else "2H",
            "event_type": "goal",
            "side": "home" if bool(event.get("isHome")) else "away",
            "score": [int(score[0]), int(score[1])],
            "goal_kind": "penalty" if "pen" in str(event.get("goalDescription") or event.get("suffix") or "").casefold() else "open_play_or_unknown",
            "player": ((event.get("player") or {}).get("name") if isinstance(event.get("player"), dict) else None) or event.get("nameStr"),
        })
    goals.sort(key=lambda row: int(row["minute"]))
    return goals


def fotmob_live_clock_seconds(hit: dict[str, Any]) -> int | None:
    status = hit.get("status") or {}
    live = status.get("liveTime") or {} if isinstance(status, dict) else {}
    long_value = str(live.get("long") or "") if isinstance(live, dict) else ""
    if ":" in long_value:
        try:
            minutes, seconds = long_value.split(":", 1)
            return int(minutes) * 60 + int(seconds)
        except (TypeError, ValueError):
            pass
    short = str(live.get("short") or "") if isinstance(live, dict) else ""
    digits = "".join(ch for ch in short if ch.isdigit())
    return int(digits) * 60 if digits else None


_365_STAT_MAP = {
    "possession": "possession",
    "total shots": "shots",
    "shots on target": "shots_on_target",
    "shots off target": "shots_off_target",
    "corners": "corners",
    "red cards": "red_cards",
    "yellow cards": "yellow_cards",
    "attacks": "attacks",
    "goal kicks": "goal_kicks",
}


def parse_365_stats_payload(payload: dict[str, Any], home_id: Any, away_id: Any) -> dict[str, tuple[float, float]]:
    values: dict[str, list[float | None]] = {}
    for row in payload.get("statistics") or []:
        if not isinstance(row, dict):
            continue
        mapped = _365_STAT_MAP.get(str(row.get("name") or "").strip().casefold())
        if not mapped:
            continue
        competitor_id = row.get("competitorId")
        side = 0 if competitor_id == home_id else (1 if competitor_id == away_id else None)
        if side is None:
            continue
        value = _number(row.get("value"))
        if value is None:
            continue
        pair = values.setdefault(mapped, [None, None])
        pair[side] = value

    out: dict[str, tuple[float, float]] = {}
    for key, pair in values.items():
        if pair[0] is None or pair[1] is None:
            continue
        out[key] = (float(pair[0]), float(pair[1]))
    return out


def parse_365_goal_timeline(game: dict[str, Any], home_id: Any, away_id: Any) -> list[dict[str, Any]]:
    goals: list[dict[str, Any]] = []
    score = [0, 0]
    events = [row for row in (game.get("events") or []) if isinstance(row, dict)]
    events.sort(key=lambda row: (float(_number(row.get("gameTime")) or 0), int(_number(row.get("order")) or 0)))
    for event in events:
        event_type = event.get("eventType") or {}
        event_id = event_type.get("id") if isinstance(event_type, dict) else event.get("eventTypeId")
        name = str((event_type.get("name") if isinstance(event_type, dict) else "") or event.get("type") or "").casefold()
        if event_id != 1 and "goal" not in name:
            continue
        competitor_id = event.get("competitorId") or (event.get("competitor") or {}).get("id")
        side = 0 if competitor_id == home_id else (1 if competitor_id == away_id else None)
        if side is None:
            continue
        score[side] += 1
        minute, display = _minute(event.get("gameTime"), event.get("addedTime"))
        subtype = str((event_type.get("subTypeName") if isinstance(event_type, dict) else "") or "")
        goals.append({
            "minute": minute,
            "display_minute": str(event.get("gameTimeDisplay") or display).replace("'", ""),
            "period": "1H" if int(_number(event.get("gameTime")) or 0) <= 45 else "2H",
            "event_type": "goal",
            "side": "home" if side == 0 else "away",
            "score": list(score),
            "goal_kind": "penalty" if "penalty" in subtype.casefold() else "open_play_or_unknown",
            "player_id": event.get("playerId"),
        })
    return goals


def scores365_precise_clock_seconds(game: dict[str, Any]) -> int | None:
    precise = game.get("preciseGameTime") or {}
    if isinstance(precise, dict):
        minutes = _number(precise.get("minutes"))
        seconds = _number(precise.get("seconds"))
        if minutes is not None:
            return int(minutes) * 60 + int(seconds or 0)
    minute = _number(game.get("gameTime"))
    return int(minute * 60) if minute is not None else None


def install() -> None:
    if getattr(FotMobProvider, "_live_guard_installed", False):
        return

    original_fotmob_enrich = FotMobProvider.enrich
    original_scores365_enrich = Scores365Provider.enrich

    def fotmob_detail(self: FotMobProvider, match_id: str) -> dict:
        now = time.time()
        cached = self._detail_cache.get(str(match_id))
        if cached and now - cached[0] < FOTMOB_DETAIL_TTL_SECONDS:
            return cached[1]
        detail: dict[str, Any] = {}
        for base in FOTMOB_BASES:
            code, data = http_json(base + "/matchDetails?" + urlencode({"matchId": match_id}), timeout=10)
            if code == 200 and isinstance(data, dict):
                detail = data
                break
        self._detail_cache[str(match_id)] = (now, detail)
        return detail

    def fotmob_enrich(self: FotMobProvider, home: str, away: str) -> ProviderMatch | None:
        result = original_fotmob_enrich(self, home, away)
        if result is None:
            return None
        hit, _score = self.find_match(home, away)
        detail = self._detail(str(result.provider_match_id))
        content = detail.get("content") or {} if isinstance(detail, dict) else {}
        lineup = content.get("lineup") if isinstance(content, dict) else None
        momentum = content.get("momentum") if isinstance(content, dict) else None
        player_stats = content.get("playerStats") if isinstance(content, dict) else None
        goals = parse_fotmob_goal_timeline(detail)
        observed_at = _iso_now()
        status = hit.get("status") or {} if isinstance(hit, dict) else {}
        meta = {
            **dict(result.meta or {}),
            "has_momentum": bool(momentum),
            "has_lineup": bool(lineup),
            "has_ratings": bool(player_stats),
            "goal_timeline": goals,
            "has_pending_var": bool(detail.get("hasPendingVAR")) if isinstance(detail, dict) else False,
            "live_clock_seconds": fotmob_live_clock_seconds(hit or {}),
            "endpoints": {
                **dict((result.meta or {}).get("endpoints") or {}),
                "daily_match": {
                    "observed_at": observed_at,
                    "score": status.get("scoreStr") if isinstance(status, dict) else None,
                    "ongoing": status.get("ongoing") if isinstance(status, dict) else None,
                },
                "match_details": {
                    "observed_at": observed_at,
                    "cache_ttl_seconds": FOTMOB_DETAIL_TTL_SECONDS,
                    "has_pending_var": bool(detail.get("hasPendingVAR")) if isinstance(detail, dict) else False,
                    "goal_count": len(goals),
                },
            },
        }
        return ProviderMatch(**{**result.__dict__, "meta": meta})

    def scores365_detail(self: Scores365Provider, game_id: str) -> dict:
        now = time.time()
        cached = self._detail_cache.get(str(game_id))
        if cached and now - cached[0] < SCORES365_DETAIL_TTL_SECONDS:
            return cached[1]
        code, data = self._get("game/", {
            "appTypeId": 5,
            "langId": 1,
            "timezoneName": "Etc/UTC",
            "gameId": game_id,
            "topBookmaker": 14,
        })
        game = (data.get("game") or {}) if code == 200 and isinstance(data, dict) else {}
        self._detail_cache[str(game_id)] = (now, game)
        return game

    def scores365_stats(self: Scores365Provider, game_id: str) -> tuple[dict[str, Any], dict[str, Any]]:
        if not hasattr(self, "_stats_cache"):
            self._stats_cache = {}
        now = time.time()
        cached = self._stats_cache.get(str(game_id))
        if cached:
            captured_at, ttl, payload = cached
            if now - captured_at < max(2.0, float(ttl)):
                return payload, {
                    "observed_at": datetime.fromtimestamp(captured_at, tz=timezone.utc).isoformat(),
                    "cache_hit": True,
                    "ttl_seconds": float(ttl),
                    "last_update_id": payload.get("lastUpdateId"),
                    "requested_update_id": payload.get("requestedUpdateId"),
                }
        code, data = self._get("game/stats/", {
            "appTypeId": 5,
            "langId": 1,
            "timezoneName": "Etc/UTC",
            "userCountryId": -1,
            "games": game_id,
        })
        payload = data if code == 200 and isinstance(data, dict) else {}
        ttl = float(_number(payload.get("ttl")) or SCORES365_STATS_DEFAULT_TTL_SECONDS)
        self._stats_cache[str(game_id)] = (now, ttl, payload)
        return payload, {
            "observed_at": datetime.fromtimestamp(now, tz=timezone.utc).isoformat(),
            "cache_hit": False,
            "http": code,
            "ttl_seconds": ttl,
            "last_update_id": payload.get("lastUpdateId"),
            "requested_update_id": payload.get("requestedUpdateId"),
        }

    def scores365_enrich(self: Scores365Provider, home: str, away: str) -> ProviderMatch | None:
        result = original_scores365_enrich(self, home, away)
        if result is None:
            return None
        hit, _score = self.find_match(home, away)
        if not isinstance(hit, dict):
            return result
        home_id = (hit.get("homeCompetitor") or {}).get("id")
        away_id = (hit.get("awayCompetitor") or {}).get("id")
        game = self._detail(str(result.provider_match_id))
        stats_payload, stats_meta = self._stats_payload(str(result.provider_match_id))
        cumulative = parse_365_stats_payload(stats_payload, home_id, away_id)
        merged_stats = dict(result.stats or {})
        # The dedicated /game/stats endpoint is authoritative for cumulative
        # counters. chartEvents remains useful only for shot-level xG metadata.
        merged_stats.update(cumulative)
        goals = parse_365_goal_timeline(game, home_id, away_id)
        observed_at = _iso_now()
        widgets = [row for row in (game.get("widgets") or []) if isinstance(row, dict)]
        meta = {
            **dict(result.meta or {}),
            "red_cards": int(sum(merged_stats.get("red_cards") or (0, 0))),
            "has_stats": bool(cumulative) or bool(game.get("hasStats")),
            "goal_timeline": goals,
            "live_clock_seconds": scores365_precise_clock_seconds(game),
            "has_momentum_widget": any(str(row.get("widgetType") or "").casefold() == "momentum" for row in widgets),
            "endpoints": {
                **dict((result.meta or {}).get("endpoints") or {}),
                "game": {
                    "observed_at": observed_at,
                    "cache_ttl_seconds": SCORES365_DETAIL_TTL_SECONDS,
                    "event_count": len(game.get("events") or []),
                    "goal_count": len(goals),
                    "precise_clock_seconds": scores365_precise_clock_seconds(game),
                },
                "game_stats": {
                    **stats_meta,
                    "stat_count": len(stats_payload.get("statistics") or []),
                },
            },
        }
        return ProviderMatch(**{**result.__dict__, "stats": merged_stats, "meta": meta})

    FotMobProvider._detail = fotmob_detail
    FotMobProvider.enrich = fotmob_enrich
    FotMobProvider._live_guard_installed = True

    Scores365Provider._detail = scores365_detail
    Scores365Provider._stats_payload = scores365_stats
    Scores365Provider.enrich = scores365_enrich
    Scores365Provider._live_guard_installed = True
