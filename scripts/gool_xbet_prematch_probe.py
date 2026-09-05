from __future__ import annotations

import json
import urllib.parse

from gool_bot2.xbet_market_pressure import decode_markets, _http_json


ROOTS = (
    "https://1xbet.com/service-api/LineFeed",
    "https://1xbet.com/LineFeed",
    "https://1xbet.fi/service-api/LineFeed",
    "https://1xbet.fi/LineFeed",
)


def _index(root: str):
    queries = (
        "sports=1&count=100&lng=en&mode=4&country=1&getEmpty=true",
        "sports=1&count=100&lng=en&tf=2200000&tz=0&mode=4&country=1&getEmpty=true",
    )
    for query in queries:
        payload = _http_json(f"{root}/Get1x2_VZip?{query}")
        value = payload.get("Value") if isinstance(payload, dict) else None
        if isinstance(value, list) and value:
            return value
    return []


def _game(root: str, event_id: str):
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
    return _http_json(f"{root}/GetGameZip?{urllib.parse.urlencode(params)}")


def main() -> None:
    for root in ROOTS:
        rows = _index(root)
        if not rows:
            print("XBET_PREMATCH_ROOT", json.dumps({"root": root, "available": False}))
            continue
        printed = 0
        for event in rows[:30]:
            if not isinstance(event, dict):
                continue
            event_id = str(event.get("I") or "")
            home = str(event.get("O1") or "")
            away = str(event.get("O2") or "")
            if not event_id or not home or not away:
                continue
            payload = _game(root, event_id)
            value = payload.get("Value") if isinstance(payload, dict) else None
            if not isinstance(value, dict):
                continue
            markets = decode_markets(value)
            one_x_two = markets.get("match_1x2") or {}
            totals = markets.get("match_total") or []
            print("XBET_PREMATCH", json.dumps({
                "root": root,
                "event_id": event_id,
                "home": home,
                "away": away,
                "1x2": one_x_two,
                "match_totals": totals[:8],
            }, ensure_ascii=False))
            printed += 1
            if printed >= 3:
                return
        print("XBET_PREMATCH_ROOT", json.dumps({"root": root, "available": True, "decoded": False}))
    raise SystemExit("No usable prematch football event decoded")


if __name__ == "__main__":
    main()
