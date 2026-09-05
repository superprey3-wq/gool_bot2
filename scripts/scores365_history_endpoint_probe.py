from __future__ import annotations

import json
import urllib.parse
import urllib.request
from typing import Any

BASE = "https://webws.365scores.com/web"
HEADERS = {"User-Agent": "Mozilla/5.0", "Accept": "*/*", "Referer": "https://www.365scores.com/"}
COMMON = {"appTypeId": 5, "langId": 1, "timezoneName": "Etc/UTC"}


def get(path: str, params: dict[str, Any]) -> tuple[int, Any, str]:
    url = BASE + "/" + path.lstrip("/") + "?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers=HEADERS)
    try:
        with urllib.request.urlopen(req, timeout=10) as response:
            raw = response.read()
            code = int(response.status)
    except Exception as exc:
        return 0, {"error": f"{type(exc).__name__}:{exc}"}, url
    try:
        return code, json.loads(raw.decode("utf-8")), url
    except Exception:
        return code, {"raw": raw.decode("utf-8", errors="replace")[:1000]}, url


def compact(payload: Any) -> dict[str, Any]:
    if not isinstance(payload, dict):
        return {"type": type(payload).__name__}
    out = {"keys": list(payload)[:30]}
    games = payload.get("games")
    if isinstance(games, list):
        out["games"] = len(games)
        out["game_ids"] = [g.get("id") for g in games[:20] if isinstance(g, dict)]
    for key in ("trends", "topTrends", "mainTrends", "previousMeetings", "recentMatches"):
        value = payload.get(key)
        if value is not None:
            out[key] = len(value) if isinstance(value, list) else type(value).__name__
    return out


def main() -> None:
    status, live, _ = get("games/", {**COMMON, "sports": 1})
    games = [g for g in (live.get("games") or []) if isinstance(g, dict) and g.get("statusGroup") == 3] if isinstance(live, dict) else []
    print("LIVE", status, len(games))
    target = None
    detail = None
    for game in games[:50]:
        gid = game.get("id")
        code, payload, _ = get("game/", {**COMMON, "gameId": gid, "topBookmaker": 14})
        node = payload.get("game") if code == 200 and isinstance(payload, dict) else None
        if isinstance(node, dict) and (node.get("hasTrends") or node.get("hasPreviousMeetings")):
            target, detail = game, node
            break
    if not target or not detail:
        print("NO_TARGET")
        return
    gid = target.get("id")
    home = detail.get("homeCompetitor") or {}
    away = detail.get("awayCompetitor") or {}
    recent = list(dict.fromkeys([*(home.get("recentMatches") or []), *(away.get("recentMatches") or [])]))[:8]
    print("TARGET", gid, home.get("name"), "-", away.get("name"), "HOME_ID", home.get("id"), "AWAY_ID", away.get("id"), "RECENT", recent, "FLAGS", detail.get("hasTrends"), detail.get("hasTopTrends"), detail.get("hasPreviousMeetings"))

    if recent:
        joined_comma = ",".join(str(x) for x in recent)
        joined_dash = "-".join(str(x) for x in recent)
        probes = [
            ("games/", {**COMMON, "gameIds": joined_comma}),
            ("games/", {**COMMON, "gamesIds": joined_comma}),
            ("games/", {**COMMON, "ids": joined_comma}),
            ("games/", {**COMMON, "games": joined_comma}),
            ("games/", {**COMMON, "gameIds": joined_dash}),
            ("games/", {**COMMON, "competitors": f"{home.get('id')},{away.get('id')}"}),
            ("games/", {**COMMON, "competitorId": home.get("id")}),
            ("games/", {**COMMON, "competitors": home.get("id")}),
        ]
        for path, params in probes:
            code, payload, url = get(path, params)
            print("HISTORY_PROBE", code, url, json.dumps(compact(payload), ensure_ascii=False))

    trend_paths = [
        "trends/", "top-trends/", "topTrends/", "game-trends/", "games/trends/", "games/top-trends/",
        "game/trend/", "game/top-trends/", "game/topTrends/", "game/insight/", "game/insights/",
        "game/previous-meetings/", "game/previousMeetings/", "game/recent-matches/", "game/recentMatches/",
    ]
    param_sets = [
        {**COMMON, "gameId": gid},
        {**COMMON, "gameId": gid, "topBookmaker": 14},
        {**COMMON, "game": gid},
        {**COMMON, "id": gid},
        {**COMMON, "sportId": 1, "gameId": gid},
    ]
    for path in trend_paths:
        for params in param_sets:
            code, payload, url = get(path, params)
            if code == 200:
                text = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
                print("TREND_HIT", url, json.dumps(compact(payload), ensure_ascii=False), text[:12000])
                break


if __name__ == "__main__":
    main()
