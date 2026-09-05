from __future__ import annotations

import json

from gool_bot2.providers.fotmob import FotMobProvider


def _find_lineup(value, path=""):
    found = []
    if isinstance(value, dict):
        for key, child in value.items():
            child_path = f"{path}/{key}"
            if "lineup" in str(key).lower():
                found.append((child_path, child))
            found.extend(_find_lineup(child, child_path))
    elif isinstance(value, list):
        for idx, child in enumerate(value[:30]):
            found.extend(_find_lineup(child, f"{path}[{idx}]"))
    return found


def _summary(value):
    if value is None:
        return {"type": "null"}
    if isinstance(value, dict):
        result = {"type": "dict", "keys": list(value.keys())[:40]}
        for key in ("homeTeam", "awayTeam", "home", "away", "players", "starters", "bench"):
            child = value.get(key)
            if isinstance(child, list):
                result[f"{key}_count"] = len(child)
            elif isinstance(child, dict):
                result[f"{key}_keys"] = list(child.keys())[:20]
        return result
    if isinstance(value, list):
        return {
            "type": "list",
            "count": len(value),
            "sample": value[:2],
        }
    return {"type": type(value).__name__, "value": str(value)[:200]}


def main() -> None:
    provider = FotMobProvider()
    rows = provider._rows("20260905")
    printed = 0
    for row in rows[:80]:
        match_id = row.get("id") or row.get("matchId")
        if not match_id:
            continue
        detail = provider._detail(str(match_id))
        if not detail:
            continue
        hits = _find_lineup(detail)
        usable = [(path, value) for path, value in hits if value not in (None, [], {})]
        if not usable:
            continue
        home = ((row.get("home") or {}).get("name") if isinstance(row.get("home"), dict) else row.get("homeName"))
        away = ((row.get("away") or {}).get("name") if isinstance(row.get("away"), dict) else row.get("awayName"))
        print("FOTMOB_LINEUP", json.dumps({
            "match_id": match_id,
            "home": home,
            "away": away,
            "paths": [{"path": path, "summary": _summary(value)} for path, value in usable[:4]],
        }, ensure_ascii=False, default=str)[:8000])
        printed += 1
        if printed >= 3:
            return
    print("FOTMOB_LINEUP none_found")


if __name__ == "__main__":
    main()
