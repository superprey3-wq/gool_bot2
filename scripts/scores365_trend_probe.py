from __future__ import annotations

import json
import os
from typing import Any

from gool_bot2.providers.scores365 import Scores365Provider


TERMS = ("trend", "insight", "recent", "previous", "meeting", "betting", "opportunity", "form")


def _walk(value: Any, path: str = "", depth: int = 0) -> list[tuple[str, Any]]:
    out: list[tuple[str, Any]] = []
    if depth > 8:
        return out
    if isinstance(value, dict):
        for key, child in value.items():
            child_path = f"{path}.{key}" if path else str(key)
            folded = child_path.casefold()
            if any(term in folded for term in TERMS):
                out.append((child_path, child))
            out.extend(_walk(child, child_path, depth + 1))
    elif isinstance(value, list):
        for index, child in enumerate(value[:8]):
            out.extend(_walk(child, f"{path}[{index}]", depth + 1))
    return out


def _preview(value: Any, limit: int = 1600) -> str:
    text = json.dumps(value, ensure_ascii=False, separators=(",", ":"), default=str)
    return text[:limit]


def main() -> int:
    provider = Scores365Provider()
    game_id = os.getenv("SCORES365_PROBE_GAME_ID", "4742078")
    code, payload = provider._get(
        "game/",
        {
            "appTypeId": 5,
            "langId": 1,
            "timezoneName": "Etc/UTC",
            "gameId": game_id,
            "topBookmaker": 14,
        },
    )
    print(f"S365_PROBE game_id={game_id} status={code}")
    if code != 200 or not isinstance(payload, dict):
        print(f"S365_PROBE_ERROR payload_type={type(payload).__name__}")
        return 1
    game = payload.get("game") or {}
    if not isinstance(game, dict):
        print("S365_PROBE_ERROR game_missing")
        return 1

    print("S365_TOP_KEYS " + " | ".join(sorted(game.keys())))
    print(
        "S365_FLAGS "
        f"hasTrends={game.get('hasTrends')} hasTopTrends={game.get('hasTopTrends')} "
        f"hasRecentMatches={game.get('hasRecentMatches')} hasPreviousMeetings={game.get('hasPreviousMeetings')}"
    )
    rows = _walk(game)
    seen: set[str] = set()
    for path, value in rows:
        if path in seen:
            continue
        seen.add(path)
        print(f"S365_PATH {path}={_preview(value)}")
        if len(seen) >= 120:
            break
    print(f"S365_DONE matching_paths={len(seen)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
