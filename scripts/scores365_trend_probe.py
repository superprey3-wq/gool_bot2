from __future__ import annotations

import json
import os
from typing import Any

from gool_bot2.providers.scores365 import Scores365Provider


KEY_TERMS = ("trend", "insight", "recentmatch", "previousmeeting", "betting", "opportunity", "prediction")


def _walk(value: Any, path: str = "", depth: int = 0) -> list[tuple[str, Any]]:
    out: list[tuple[str, Any]] = []
    if depth > 10:
        return out
    if isinstance(value, dict):
        for key, child in value.items():
            child_path = f"{path}.{key}" if path else str(key)
            folded = str(key).replace("_", "").casefold()
            if any(term in folded for term in KEY_TERMS):
                out.append((child_path, child))
            out.extend(_walk(child, child_path, depth + 1))
    elif isinstance(value, list):
        for index, child in enumerate(value[:30]):
            out.extend(_walk(child, f"{path}[{index}]", depth + 1))
    return out


def _preview(value: Any, limit: int = 2600) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), default=str)[:limit]


def _dump(label: str, payload: Any) -> None:
    rows = _walk(payload)
    seen: set[str] = set()
    for path, value in rows:
        if path in seen:
            continue
        seen.add(path)
        print(f"S365_{label}_PATH {path}={_preview(value)}")
    print(f"S365_{label}_DONE matching_paths={len(seen)}")


def main() -> int:
    provider = Scores365Provider()
    game_id = os.getenv("SCORES365_PROBE_GAME_ID", "4742078")
    code, payload = provider._get("game/", {"appTypeId":5,"langId":1,"timezoneName":"Etc/UTC","gameId":game_id,"topBookmaker":103})
    print(f"S365_PROBE game_id={game_id} status={code}")
    if code != 200 or not isinstance(payload, dict):
        return 1
    game = payload.get("game") or {}
    print("S365_TOP_KEYS " + " | ".join(sorted(game.keys())))
    print(f"S365_FLAGS hasTrends={game.get('hasTrends')} hasTopTrends={game.get('hasTopTrends')} hasRecentMatches={game.get('hasRecentMatches')} hasPreviousMeetings={game.get('hasPreviousMeetings')}")
    _dump("GAME", game)

    home_id = (game.get("homeCompetitor") or {}).get("id") or 107
    for label, path, params in (
        ("CURRENT", "games/current/", {"appTypeId":5,"langId":1,"timezoneName":"Etc/UTC","userCountryId":333,"competitors":home_id,"showOdds":"true","includeTopBettingOpportunity":1,"topBookmaker":103}),
        ("FEATURED", "games/featured/", {"appTypeId":5,"langId":1,"timezoneName":"Etc/UTC","userCountryId":333,"competitors":home_id,"showOdds":"true","numberOfGames":3,"context":4,"topBookmaker":103}),
    ):
        c, p = provider._get(path, params)
        print(f"S365_{label} status={c}")
        if c == 200:
            _dump(label, p)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
