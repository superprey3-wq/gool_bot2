from __future__ import annotations

import json
import urllib.parse
import urllib.request
from typing import Any

BASE = "https://webws.365scores.com/web"
HEADERS = {"User-Agent": "Mozilla/5.0", "Accept": "*/*", "Referer": "https://www.365scores.com/"}
COMMON = {"appTypeId": 5, "langId": 1, "timezoneName": "Etc/UTC", "userCountryId": 1}


def get(path: str, params: dict[str, Any]) -> tuple[int, Any, str]:
    url = BASE + "/" + path.lstrip("/") + "?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers=HEADERS)
    try:
        with urllib.request.urlopen(req, timeout=12) as response:
            raw = response.read()
            code = int(response.status)
    except Exception as exc:
        return 0, {"error": f"{type(exc).__name__}:{exc}"}, url
    try:
        return code, json.loads(raw.decode("utf-8")), url
    except Exception:
        return code, {"raw": raw.decode("utf-8", errors="replace")[:2000]}, url


def compact(payload: Any) -> dict[str, Any]:
    if not isinstance(payload, dict):
        return {"type": type(payload).__name__}
    out: dict[str, Any] = {"keys": list(payload)[:50]}
    for key in ("statistics", "trends", "topTrends", "h2hGames", "games", "competitors", "bookmakers"):
        value = payload.get(key)
        if isinstance(value, list):
            out[key] = len(value)
    game = payload.get("game")
    if isinstance(game, dict):
        out["game_keys"] = list(game)[:70]
        for key in ("h2hGames", "trends", "topTrends"):
            value = game.get(key)
            if isinstance(value, list):
                out[f"game.{key}"] = len(value)
    return out


def main() -> None:
    code, live, _ = get("games/", {**COMMON, "sports": 1})
    games = [g for g in (live.get("games") or []) if isinstance(g, dict)] if code == 200 and isinstance(live, dict) else []
    target = None
    detail = None
    for hit in games:
        if int(hit.get("statusGroup") or 0) not in (1, 2, 3):
            continue
        status, payload, _ = get("game/", {**COMMON, "gameId": hit.get("id"), "topBookmaker": 14})
        node = payload.get("game") if status == 200 and isinstance(payload, dict) else None
        if isinstance(node, dict) and (node.get("hasTrends") or node.get("hasPreviousMeetings")):
            target, detail = hit, node
            break
    if not target or not detail:
        raise SystemExit("No target with trends/H2H flags")

    gid = int(detail["id"])
    home_id = int((detail.get("homeCompetitor") or {}).get("id") or 0)
    away_id = int((detail.get("awayCompetitor") or {}).get("id") or 0)
    competition_id = int(detail.get("competitionId") or 0)
    matchup = f"{home_id}-{away_id}-{competition_id}"
    matchup_short = f"{home_id}-{away_id}"
    print("TARGET", gid, (detail.get("homeCompetitor") or {}).get("name"), "-", (detail.get("awayCompetitor") or {}).get("name"), "MATCHUP", matchup, "FLAGS", detail.get("hasTrends"), detail.get("hasTopTrends"), detail.get("hasPreviousMeetings"))

    probes = [
        ("stats/preGame/", {**COMMON, "game": gid, "onlyMajor": "false", "topBookmaker": 14}),
        ("stats/preGame/", {**COMMON, "gameId": gid, "onlyMajor": "false", "topBookmaker": 14}),
        ("stats/pregame/", {**COMMON, "game": gid, "onlyMajor": "false", "topBookmaker": 14}),
        ("stats/pre-game/", {**COMMON, "game": gid, "onlyMajor": "false", "topBookmaker": 14}),
        ("games/h2h/", {**COMMON, "gameId": gid, "matchupId": matchup, "topBookmaker": 14}),
        ("games/h2h/", {**COMMON, "gameId": gid, "matchupId": matchup_short, "topBookmaker": 14}),
        ("games/h2h/", {**COMMON, "game": gid, "matchupId": matchup, "topBookmaker": 14}),
        ("trends/", {**COMMON, "gameId": gid, "matchupId": matchup, "topBookmaker": 14}),
        ("trends/", {**COMMON, "game": gid, "topBookmaker": 14}),
        ("bets/trends/", {**COMMON, "gameId": gid, "matchupId": matchup, "topBookmaker": 14}),
        ("betting/trends/", {**COMMON, "gameId": gid, "matchupId": matchup, "topBookmaker": 14}),
        ("games/trends/", {**COMMON, "gameId": gid, "matchupId": matchup, "topBookmaker": 14}),
    ]
    for path, params in probes:
        status, payload, url = get(path, params)
        summary = compact(payload)
        print("PROBE", status, url, json.dumps(summary, ensure_ascii=False))
        if status == 200:
            print("RAW200", path, json.dumps(payload, ensure_ascii=False, separators=(",", ":"))[:30000])


if __name__ == "__main__":
    main()
