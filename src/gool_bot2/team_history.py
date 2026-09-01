from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from urllib.parse import urlencode

from .providers.common import http_json, pair_score

FOTMOB_BASES = ("https://www.fotmob.com/api/data", "https://www.fotmob.com/api")


def _name(value: Any) -> str:
    if isinstance(value, dict):
        return str(value.get("name") or value.get("teamName") or value.get("shortName") or "").strip()
    return str(value or "").strip()


def _number(value: Any) -> int | None:
    if isinstance(value, dict):
        for key in ("current", "score", "value"):
            if value.get(key) not in (None, "", "-"):
                value = value.get(key)
                break
    try:
        if value in (None, "", "-"):
            return None
        return int(float(value))
    except (TypeError, ValueError):
        return None


def _score(row: dict[str, Any], side: str) -> int | None:
    team = row.get(side)
    if isinstance(team, dict):
        for key in ("score", "currentScore", "goals"):
            value = _number(team.get(key))
            if value is not None:
                return value
    for key in (f"{side}Score", f"{side}_score"):
        value = _number(row.get(key))
        if value is not None:
            return value
    score = row.get("score")
    if isinstance(score, dict):
        return _number(score.get(side))
    return None


def _finished(row: dict[str, Any]) -> bool:
    status = row.get("status")
    if isinstance(status, dict):
        if bool(status.get("finished")):
            return True
        reason = status.get("reason")
        if isinstance(reason, dict):
            reason = reason.get("short") or reason.get("long")
        reason_text = str(reason or "").lower()
        if any(token in reason_text for token in ("ft", "full time", "full-time", "finished", "after penalties", "aet")):
            return True
    text = " ".join(str(row.get(k) or "") for k in ("status", "state", "reason")).lower()
    return any(token in text for token in ("finished", "full time", "full-time", " ft "))


def _timestamp(row: dict[str, Any]) -> str:
    value = row.get("utcTime") or row.get("timeTS") or row.get("timestamp") or row.get("date") or row.get("startTime") or ""
    return str(value)


def _sort_stamp(value: str) -> float:
    try:
        raw = float(value)
        return raw / 1000.0 if raw > 10_000_000_000 else raw
    except (TypeError, ValueError):
        pass
    try:
        text = str(value).replace("Z", "+00:00")
        return datetime.fromisoformat(text).astimezone(timezone.utc).timestamp()
    except Exception:
        return 0.0


def _extract_finished_matches(payload: Any, source: str) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    seen: set[tuple[str, str, int, int, str]] = set()

    def walk(value: Any) -> None:
        if isinstance(value, dict):
            home = _name(value.get("home") or value.get("homeTeam") or value.get("home_team"))
            away = _name(value.get("away") or value.get("awayTeam") or value.get("away_team"))
            hs = _score(value, "home")
            aws = _score(value, "away")
            if home and away and hs is not None and aws is not None and _finished(value):
                stamp = _timestamp(value)
                identity = (home.casefold(), away.casefold(), hs, aws, stamp)
                if identity not in seen:
                    seen.add(identity)
                    rows.append({
                        "home": home,
                        "away": away,
                        "home_score": hs,
                        "away_score": aws,
                        "timestamp": stamp,
                        "source": source,
                    })
            for child in value.values():
                walk(child)
        elif isinstance(value, list):
            for child in value:
                walk(child)

    walk(payload)
    rows.sort(key=lambda r: _sort_stamp(str(r.get("timestamp") or "")), reverse=True)
    return rows


def _fotmob_team_payload(team_id: Any) -> dict[str, Any]:
    if team_id in (None, ""):
        return {}
    params = urlencode({"id": team_id, "ccode3": "INT"})
    for base in FOTMOB_BASES:
        code, data = http_json(base + "/teams?" + params, timeout=12)
        if code == 200 and isinstance(data, dict) and data:
            return data
    return {}


def _similar(name: str, target: str) -> bool:
    if not name or not target:
        return False
    return pair_score(target, target, name, name) >= 0.72


def fotmob_team_history(fotmob_provider: Any, home: str, away: str, limit: int = 10) -> dict[str, Any]:
    """Fetch recent results from FotMob's team endpoint for both current teams.

    This is deliberately independent from matchDetails/H2H. The `/teams` endpoint
    contains team overview, fixtures and history, so it is a much stronger fallback
    when Flashscore's df_hh feed is empty or changes its section format.
    """
    hit, match_score = fotmob_provider.find_match(home, away)
    if not hit:
        return {"source": "fotmob_team_history", "raw_matches": 0, "match_score": match_score}

    home_node = hit.get("home") if isinstance(hit.get("home"), dict) else {}
    away_node = hit.get("away") if isinstance(hit.get("away"), dict) else {}
    home_id = home_node.get("id") or hit.get("homeId")
    away_id = away_node.get("id") or hit.get("awayId")
    home_payload = _fotmob_team_payload(home_id)
    away_payload = _fotmob_team_payload(away_id)

    home_rows = _extract_finished_matches(home_payload, "fotmob_team")
    away_rows = _extract_finished_matches(away_payload, "fotmob_team")
    combined: list[dict[str, Any]] = []
    seen: set[tuple[str, str, int, int, str]] = set()
    for row in home_rows + away_rows:
        identity = (
            str(row.get("home") or "").casefold(),
            str(row.get("away") or "").casefold(),
            int(row.get("home_score") or 0),
            int(row.get("away_score") or 0),
            str(row.get("timestamp") or ""),
        )
        if identity not in seen:
            seen.add(identity)
            combined.append(row)
    combined.sort(key=lambda r: _sort_stamp(str(r.get("timestamp") or "")), reverse=True)

    def has_team(row: dict[str, Any], team: str) -> bool:
        return _similar(str(row.get("home") or ""), team) or _similar(str(row.get("away") or ""), team)

    home_recent = [r for r in combined if has_team(r, home)][:limit]
    away_recent = [r for r in combined if has_team(r, away)][:limit]
    home_at_home = [r for r in home_recent if _similar(str(r.get("home") or ""), home)][:limit]
    away_away = [r for r in away_recent if _similar(str(r.get("away") or ""), away)][:limit]
    h2h = [r for r in combined if has_team(r, home) and has_team(r, away)][:limit]
    return {
        "source": "fotmob_team_history",
        "home_recent": home_recent,
        "away_recent": away_recent,
        "home_at_home": home_at_home,
        "away_away": away_away,
        "h2h": h2h,
        "raw_matches": len(combined),
        "match_score": round(float(match_score or 0.0), 3),
        "home_team_id": home_id,
        "away_team_id": away_id,
    }
