from __future__ import annotations

import json
import time
from urllib.parse import urlencode

from .common import ProviderMatch, http_json, pair_score

BASES = ("https://www.fotmob.com/api/data", "https://www.fotmob.com/api")


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
        if cached and now - cached[0] < 150:
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

    def enrich(self, home: str, away: str) -> ProviderMatch | None:
        hit, score = self.find_match(home, away)
        if not hit:
            return None
        match_id = hit.get("id") or hit.get("matchId")
        if not match_id:
            return None
        detail = self._detail(str(match_id))
        shots = (((detail.get("content") or {}).get("shotmap") or {}).get("shots") or []) if isinstance(detail, dict) else []
        xg_home = xg_away = xgot_home = xgot_away = 0.0
        has_xg = has_xgot = False
        for shot in shots:
            if not isinstance(shot, dict):
                continue
            is_home = bool(shot.get("isHome") or shot.get("teamId") == (hit.get("home") or {}).get("id"))
            for key in ("expectedGoals", "xG"):
                if shot.get(key) is not None:
                    try:
                        value = float(shot[key]); has_xg = True
                        if is_home: xg_home += value
                        else: xg_away += value
                    except Exception:
                        pass
                    break
            for key in ("expectedGoalsOnTarget", "xGOT"):
                if shot.get(key) is not None:
                    try:
                        value = float(shot[key]); has_xgot = True
                        if is_home: xgot_home += value
                        else: xgot_away += value
                    except Exception:
                        pass
                    break
        stats: dict[str, tuple[float, float]] = {}
        if has_xg:
            stats["xg"] = (round(xg_home, 3), round(xg_away, 3))
        if has_xgot:
            stats["xgot"] = (round(xgot_home, 3), round(xgot_away, 3))
        stats["shotmap_shots"] = (
            float(sum(1 for s in shots if isinstance(s, dict) and bool(s.get("isHome")))),
            float(sum(1 for s in shots if isinstance(s, dict) and not bool(s.get("isHome")))),
        )
        blob = json.dumps(detail, ensure_ascii=False).lower()
        return ProviderMatch(
            provider=self.name,
            provider_match_id=str(match_id),
            home=home,
            away=away,
            stats=stats,
            meta={
                "match_score": round(score, 3),
                "has_momentum": "momentum" in blob,
                "has_lineup": "lineup" in blob,
                "has_ratings": "rating" in blob,
                "has_shotmap": bool(shots),
            },
        )
