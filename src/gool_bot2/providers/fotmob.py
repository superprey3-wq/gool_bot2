from __future__ import annotations

import json
import math
import time
from urllib.parse import urlencode
from typing import Any

from .common import ProviderMatch, http_json, pair_score

BASES = ("https://www.fotmob.com/api/data", "https://www.fotmob.com/api")


def _num(value: Any) -> float | None:
    try:
        if value in (None, "", "-"):
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _truthy(shot: dict[str, Any], *keys: str) -> bool:
    for key in keys:
        value = shot.get(key)
        if isinstance(value, bool) and value:
            return True
        if isinstance(value, (int, float)) and value != 0:
            return True
        if isinstance(value, str) and value.strip().lower() in {"true", "yes", "1", "big chance", "bigchance"}:
            return True
    return False


def _inside_box(shot: dict[str, Any]) -> bool:
    if _truthy(shot, "isInsideBox", "insideBox", "isInBox"):
        return True
    situation = " ".join(str(shot.get(k) or "") for k in ("situation", "shotType", "eventType", "area")).lower()
    if "inside" in situation and "box" in situation:
        return True
    x = _num(shot.get("x")); y = _num(shot.get("y"))
    if x is None or y is None or not (0 <= x <= 105 and 0 <= y <= 68):
        return False
    return x >= 88.5 and 13.84 <= y <= 54.16


def _on_target(shot: dict[str, Any]) -> bool:
    if _truthy(shot, "isOnTarget", "onTarget"):
        return True
    label = " ".join(str(shot.get(k) or "") for k in ("eventType", "shotType", "result")).lower()
    return any(token in label for token in ("goal", "save", "saved", "on target"))


def _team_name(value: Any) -> str:
    if isinstance(value, dict):
        return str(value.get("name") or value.get("teamName") or "").strip()
    return str(value or "").strip()


def _score_value(node: dict[str, Any], side: str) -> int | None:
    keys = (f"{side}Score", f"{side}_score", "score")
    for key in keys:
        value = node.get(key)
        if key == "score" and isinstance(value, dict):
            value = value.get(side) or value.get("home" if side == "home" else "away")
        if isinstance(value, dict):
            value = value.get("current") or value.get("score") or value.get("value")
        try:
            if value not in (None, "", "-"):
                return int(float(value))
        except (TypeError, ValueError):
            pass
    return None


def _embedded_history(detail: dict[str, Any], home: str, away: str, limit: int = 10) -> dict[str, Any]:
    """Extract finished form/H2H rows already embedded in FotMob match details.

    We only accept rows found below history-like keys (form/h2h/recent/previous)
    so the current live match cannot leak into the pre-match profile.
    """
    rows: list[dict[str, Any]] = []
    seen: set[tuple[str, str, int, int, str]] = set()

    def walk(value: Any, path: str = "") -> None:
        if isinstance(value, dict):
            lower_path = path.lower()
            hist_path = any(token in lower_path for token in ("h2h", "headtohead", "form", "recent", "previous", "lastmatch", "lastmatches"))
            h = _team_name(value.get("home") or value.get("homeTeam") or value.get("home_team"))
            a = _team_name(value.get("away") or value.get("awayTeam") or value.get("away_team"))
            hs = _score_value(value, "home"); aws = _score_value(value, "away")
            if hist_path and h and a and hs is not None and aws is not None:
                stamp = str(value.get("utcTime") or value.get("timeTS") or value.get("timestamp") or value.get("date") or "")
                key = (h.casefold(), a.casefold(), hs, aws, stamp)
                if key not in seen:
                    seen.add(key)
                    rows.append({"home": h, "away": a, "home_score": hs, "away_score": aws, "timestamp": stamp, "source": "fotmob"})
            for key, child in value.items():
                walk(child, f"{path}/{key}")
        elif isinstance(value, list):
            for child in value:
                walk(child, path)

    walk(detail)
    home_cf = home.casefold().strip(); away_cf = away.casefold().strip()
    def match_team(name: str, target: str) -> bool:
        return pair_score(target, target, name, name) >= 0.72
    def has_team(row: dict[str, Any], target: str) -> bool:
        return match_team(str(row.get("home") or ""), target) or match_team(str(row.get("away") or ""), target)
    home_recent = [r for r in rows if has_team(r, home_cf)][:limit]
    away_recent = [r for r in rows if has_team(r, away_cf)][:limit]
    h2h = [r for r in rows if has_team(r, home_cf) and has_team(r, away_cf)][:limit]
    home_at_home = [r for r in home_recent if match_team(str(r.get("home") or ""), home_cf)][:limit]
    away_away = [r for r in away_recent if match_team(str(r.get("away") or ""), away_cf)][:limit]
    return {"source": "fotmob_embedded", "home_recent": home_recent, "away_recent": away_recent, "home_at_home": home_at_home, "away_away": away_away, "h2h": h2h, "raw_matches": len(rows)}


class FotMobProvider:
    name = "fotmob"

    def __init__(self) -> None:
        self._daily_cache: dict[str, tuple[float, list[dict]]] = {}
        self._detail_cache: dict[str, tuple[float, dict]] = {}

    def _rows(self, date_key: str) -> list[dict]:
        now = time.time()
        cached = self._daily_cache.get(date_key)
        if cached and now - cached[0] < 300:
            return cached[1]
        rows: list[dict] = []
        for base in BASES:
            code, data = http_json(base + "/matches?" + urlencode({"date": date_key}), timeout=10)
            if code != 200 or not isinstance(data, dict):
                continue
            for league in data.get("leagues") or []:
                if isinstance(league, dict):
                    rows.extend([m for m in league.get("matches") or [] if isinstance(m, dict)])
            if rows:
                break
        self._daily_cache[date_key] = (now, rows)
        return rows

    def _detail(self, match_id: str) -> dict:
        now = time.time()
        cached = self._detail_cache.get(str(match_id))
        if cached and now - cached[0] < 120:
            return cached[1]
        detail: dict = {}
        for base in BASES:
            code, data = http_json(base + "/matchDetails?" + urlencode({"matchId": match_id}), timeout=10)
            if code == 200 and isinstance(data, dict):
                detail = data
                break
        self._detail_cache[str(match_id)] = (now, detail)
        return detail

    def find_match(self, home: str, away: str, date_key: str | None = None) -> tuple[dict | None, float]:
        date_key = date_key or time.strftime("%Y%m%d", time.gmtime())
        best: dict | None = None
        best_score = 0.0
        for row in self._rows(date_key):
            h = (row.get("home") or {}).get("name") if isinstance(row.get("home"), dict) else row.get("homeName")
            a = (row.get("away") or {}).get("name") if isinstance(row.get("away"), dict) else row.get("awayName")
            score = pair_score(home, away, str(h or ""), str(a or ""))
            if score > best_score:
                best, best_score = row, score
        return (best, best_score) if best_score >= 0.72 else (None, best_score)

    def prematch_context(self, home: str, away: str, limit: int = 10) -> dict[str, Any]:
        hit, score = self.find_match(home, away)
        if not hit:
            return {"source": "fotmob_embedded", "raw_matches": 0, "match_score": score}
        match_id = hit.get("id") or hit.get("matchId")
        if not match_id:
            return {"source": "fotmob_embedded", "raw_matches": 0, "match_score": score}
        ctx = _embedded_history(self._detail(str(match_id)), home, away, limit=limit)
        ctx["match_score"] = round(score, 3)
        return ctx

    def enrich(self, home: str, away: str) -> ProviderMatch | None:
        hit, score = self.find_match(home, away)
        if not hit:
            return None
        match_id = hit.get("id") or hit.get("matchId")
        if not match_id:
            return None
        detail = self._detail(str(match_id))
        shots = (((detail.get("content") or {}).get("shotmap") or {}).get("shots") or []) if isinstance(detail, dict) else []
        home_id = (hit.get("home") or {}).get("id") if isinstance(hit.get("home"), dict) else None

        xg = [0.0, 0.0]; xgot = [0.0, 0.0]; shot_counts = [0.0, 0.0]
        sot = [0.0, 0.0]; inside = [0.0, 0.0]; big = [0.0, 0.0]
        blocked = [0.0, 0.0]; high_xg = [0.0, 0.0]
        has_xg = has_xgot = False
        shot_quality: list[dict[str, Any]] = []

        for shot in shots:
            if not isinstance(shot, dict):
                continue
            team_id = shot.get("teamId")
            is_home = bool(shot.get("isHome")) if shot.get("isHome") is not None else (team_id == home_id)
            side = 0 if is_home else 1
            shot_counts[side] += 1
            xv = next((_num(shot.get(k)) for k in ("expectedGoals", "xG") if _num(shot.get(k)) is not None), None)
            xgotv = next((_num(shot.get(k)) for k in ("expectedGoalsOnTarget", "xGOT") if _num(shot.get(k)) is not None), None)
            if xv is not None:
                xg[side] += xv; has_xg = True
                if xv >= 0.20: high_xg[side] += 1
            if xgotv is not None:
                xgot[side] += xgotv; has_xgot = True
            if _on_target(shot): sot[side] += 1
            if _inside_box(shot): inside[side] += 1
            if _truthy(shot, "isBigChance", "bigChance", "bigChanceCreated"): big[side] += 1
            if _truthy(shot, "isBlocked", "blocked"): blocked[side] += 1
            shot_quality.append({"side": "home" if side == 0 else "away", "minute": shot.get("min") or shot.get("minute"), "xg": xv, "xgot": xgotv, "inside_box": _inside_box(shot), "on_target": _on_target(shot), "big_chance": _truthy(shot, "isBigChance", "bigChance", "bigChanceCreated"), "x": _num(shot.get("x")), "y": _num(shot.get("y"))})

        stats: dict[str, tuple[float, float]] = {
            "shotmap_shots": (shot_counts[0], shot_counts[1]), "shots": (shot_counts[0], shot_counts[1]),
            "shots_on_target": (sot[0], sot[1]), "shots_inside_box": (inside[0], inside[1]),
            "big_chances": (big[0], big[1]), "blocked_shots": (blocked[0], blocked[1]),
            "high_xg_shots": (high_xg[0], high_xg[1]),
        }
        if has_xg: stats["xg"] = (round(xg[0], 3), round(xg[1], 3))
        if has_xgot: stats["xgot"] = (round(xgot[0], 3), round(xgot[1], 3))

        blob = json.dumps(detail, ensure_ascii=False).lower()
        return ProviderMatch(provider=self.name, provider_match_id=str(match_id), home=home, away=away, stats=stats, meta={"match_score": round(score, 3), "has_momentum": "momentum" in blob, "has_lineup": "lineup" in blob, "has_ratings": "rating" in blob, "has_shotmap": bool(shots), "shot_quality": shot_quality[-40:]})
