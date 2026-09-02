from __future__ import annotations

import urllib.parse
from typing import Any

import final_eight_sources_live_audit as eight
import xbet_specific_markets_live_audit as core

# Field mapping verified from current football GetGameZip payload structure.
core.MARKET_TYPES = {
    "match_total": {"over": 9, "under": 10},
    "home_team_total": {"over": 11, "under": 12},
    "away_team_total": {"over": 13, "under": 14},
    "btts": {"yes": 182, "no": 183},
}


def fetch_game_v2(preferred_root: str, event_id: str) -> dict[str, Any]:
    params = {
        "id": event_id,
        "lng": "en",
        "cfview": 0,
        "isSubGames": "true",
        "GroupEvents": "true",
        "allEventsGroupSubGames": "true",
        "countevents": 250,
        "grMode": 2,
    }
    roots = [preferred_root] + [r for r in core.ROOTS if r != preferred_root]
    attempts = []
    for root in roots:
        url = f"{root}/GetGameZip?{urllib.parse.urlencode(params)}"
        r = eight.http_json(url, core.HEADERS, 30)
        attempts.append({k: v for k, v in r.items() if k != "payload"})
        if r.get("state") != "ok":
            continue
        payload = r.get("payload")
        value = payload.get("Value") if isinstance(payload, dict) else None
        if isinstance(value, dict):
            return {"state": "ok", "error": None, "url": url, "value": value, "attempts": attempts}
    err = attempts[-1].get("error") if attempts else "no attempts"
    state = "blocked" if attempts and all(a.get("state") == "blocked" for a in attempts) else "error"
    return {"state": state, "error": err, "url": "", "value": None, "attempts": attempts}


def decode_markets_v2(game: dict[str, Any], fs: dict[str, Any]) -> dict[str, Any]:
    nodes = core.collect_nodes(game)
    match_rows = core.pairs(nodes, 9, 10)
    home_rows = core.pairs(nodes, 11, 12)
    away_rows = core.pairs(nodes, 13, 14)
    hs = int(fs.get("home_score") or 0)
    aas = int(fs.get("away_score") or 0)
    return {
        "node_count": len(nodes),
        "match_total_over": match_rows,
        "match_total_focus": core.nearest_actionable(match_rows, hs + aas),
        "btts": core.btts(nodes),
        "home_team_total_over": home_rows,
        "home_team_total_focus": core.nearest_actionable(home_rows, hs),
        "away_team_total_over": away_rows,
        "away_team_total_focus": core.nearest_actionable(away_rows, aas),
        "type_counts": {
            str(t): sum(1 for n in nodes if not n.get("in_subgame") and int(n.get("T") or -1) == t)
            for t in (9, 10, 11, 12, 13, 14, 182, 183)
        },
        "validated_groups": {
            "match_total": 4,
            "home_team_total": 5,
            "away_team_total": 6,
            "btts": 22,
        },
    }


core.fetch_game = fetch_game_v2
core.decode_markets = decode_markets_v2

if __name__ == "__main__":
    raise SystemExit(core.main())
