from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from gool_bot2.providers.flashscore import FlashscoreProvider
from gool_bot2.xbet_market_robust import RobustXBetMarketCollector


def _compact(obj: Any, limit: int = 9000) -> str:
    text = json.dumps(obj, ensure_ascii=False, separators=(",", ":"), default=str)
    return text if len(text) <= limit else text[:limit] + "...<truncated>"


def main() -> None:
    collector = RobustXBetMarketCollector(Path("/tmp/first_half_probe_state.json"), Path("/tmp/first_half_probe_history.jsonl"))
    _, index = collector._fetch_index()
    live = [m for m in FlashscoreProvider().live_matches() if 0 < int(m.minute or 0) <= 45]
    print(f"FIRST_HALF_PROBE flashscore_first_half={len(live)} index_events={len(index)}")
    shown = 0
    for match in live:
        event_id = collector._map(match, index)
        if not event_id:
            continue
        game = collector._game(event_id)
        if not isinstance(game, dict):
            continue
        sg = game.get("SG")
        print(
            "FIRST_HALF_GAME",
            json.dumps(
                {
                    "flashscore_id": str(match.provider_match_id),
                    "xbet_id": str(event_id),
                    "match": f"{match.home} - {match.away}",
                    "minute": int(match.minute or 0),
                    "score": [int(match.home_score or 0), int(match.away_score or 0)],
                    "game_keys": list(game.keys()),
                    "sg_type": type(sg).__name__,
                    "sg_count": len(sg) if isinstance(sg, list) else None,
                },
                ensure_ascii=False,
            ),
        )
        if sg is not None:
            print("FIRST_HALF_SG", _compact(sg))
        else:
            print("FIRST_HALF_GAME_COMPACT", _compact(game))
        shown += 1
        if shown >= 2:
            break
    print(f"FIRST_HALF_PROBE shown={shown}")


if __name__ == "__main__":
    main()
