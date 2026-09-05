from __future__ import annotations

import json
import urllib.parse
import urllib.request
from typing import Any

BASE = "https://webws.365scores.com/web"
HEADERS = {
    "User-Agent": "Mozilla/5.0",
    "Accept": "*/*",
    "Referer": "https://www.365scores.com/",
}
COMMON = {
    "appTypeId": 5,
    "langId": 1,
    "timezoneName": "Etc/UTC",
}


def get(path: str, params: dict[str, Any]) -> tuple[int, Any]:
    url = BASE + "/" + path.lstrip("/") + "?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers=HEADERS)
    try:
        with urllib.request.urlopen(req, timeout=15) as response:
            raw = response.read()
            code = int(response.status)
    except Exception as exc:
        return 0, {"error": f"{type(exc).__name__}:{exc}", "url": url}
    try:
        return code, json.loads(raw.decode("utf-8"))
    except Exception:
        return code, {"raw": raw.decode("utf-8", errors="replace")[:10000], "url": url}


def interesting_paths(value: Any, path: str = "$") -> list[str]:
    out: list[str] = []
    if isinstance(value, dict):
        for key, child in value.items():
            next_path = f"{path}.{key}"
            lower = str(key).lower()
            if any(token in lower for token in ("trend", "h2h", "headtohead", "half", "score", "form", "recent", "previous", "lastmatch")):
                out.append(next_path)
            out.extend(interesting_paths(child, next_path))
    elif isinstance(value, list):
        for index, child in enumerate(value[:30]):
            out.extend(interesting_paths(child, f"{path}[{index}]"))
    return out


def summary(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return {"type": "dict", "keys": list(value)[:80], "interesting_paths": interesting_paths(value)[:200]}
    if isinstance(value, list):
        return {"type": "list", "length": len(value), "first_type": type(value[0]).__name__ if value else None}
    return {"type": type(value).__name__, "repr": repr(value)[:500]}


def main() -> None:
    code, live = get("games/", {**COMMON, "sports": 1})
    print("LIVE_STATUS", code)
    games = [g for g in (live.get("games") or []) if isinstance(g, dict)] if isinstance(live, dict) else []
    live_games = [g for g in games if int(g.get("statusGroup") or 0) == 3]
    pool = live_games or games
    print("GAMES", len(games), "LIVE", len(live_games))
    candidates = pool[:8]
    print("CANDIDATES", json.dumps([
        {
            "id": g.get("id"),
            "home": (g.get("homeCompetitor") or {}).get("name"),
            "away": (g.get("awayCompetitor") or {}).get("name"),
            "statusGroup": g.get("statusGroup"),
        }
        for g in candidates
    ], ensure_ascii=False))

    endpoint_candidates = (
        "game/",
        "game/stats/",
        "game/trends/",
        "game/h2h/",
        "game/head-to-head/",
        "game/insights/",
        "game/previews/",
        "game/predictions/",
    )
    for game in candidates[:3]:
        gid = game.get("id")
        if not gid:
            continue
        print("\n=== GAME", gid, (game.get("homeCompetitor") or {}).get("name"), "-", (game.get("awayCompetitor") or {}).get("name"), "===")
        for endpoint in endpoint_candidates:
            params = {**COMMON, "gameId": gid, "topBookmaker": 14}
            status, payload = get(endpoint, params)
            print("ENDPOINT", endpoint, "STATUS", status, "SUMMARY", json.dumps(summary(payload), ensure_ascii=False))
            if endpoint in {"game/", "game/trends/", "game/h2h/", "game/head-to-head/", "game/insights/"}:
                text = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
                lowered = text.lower()
                if any(token in lowered for token in ("trend", "headtohead", "h2h", "halftime", "half time", "firsthalf", "first half")):
                    print("MATCHED_RAW", endpoint, text[:20000])


if __name__ == "__main__":
    main()
