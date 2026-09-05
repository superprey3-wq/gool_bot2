from __future__ import annotations

import json
from collections import defaultdict

from gool_bot2.xbet_market_pressure import XBetMarketCollector, _nodes


def main() -> None:
    collector = XBetMarketCollector.__new__(XBetMarketCollector)
    # Minimal fields needed by helper methods; avoid starting collector loops.
    from gool_bot2.providers.flashscore import FlashscoreProvider

    collector.flashscore = FlashscoreProvider()
    collector.active_root = "https://1xbet.com/service-api/LiveFeed"
    collector.mapping = {}
    collector.snapshots = {}

    root, index = XBetMarketCollector._fetch_index(collector)
    if not root or not index:
        raise SystemExit("1xBet live index unavailable")

    printed = 0
    for event in index[:80]:
        game = XBetMarketCollector._game(collector, str(event["event_id"]))
        if not isinstance(game, dict):
            continue
        nodes = [n for n in _nodes(game) if not n.get("sub") and n.get("P") is None]
        groups: dict[int, list[dict]] = defaultdict(list)
        for node in nodes:
            try:
                groups[int(node.get("G") or -1)].append(node)
            except (TypeError, ValueError):
                continue

        candidates = []
        for group_id, rows in groups.items():
            by_t = {int(r.get("T") or -1): r for r in rows}
            if {1, 2, 3}.issubset(by_t):
                candidates.append({
                    "group": group_id,
                    "t1": float(by_t[1]["C"]),
                    "t2": float(by_t[2]["C"]),
                    "t3": float(by_t[3]["C"]),
                    "paths": [by_t[1].get("path"), by_t[2].get("path"), by_t[3].get("path")],
                })
        if not candidates:
            continue

        print("XBET_1X2_PROBE", json.dumps({
            "root": root,
            "event_id": event["event_id"],
            "home": event["home"],
            "away": event["away"],
            "score": [game.get("SC", {}).get("FS", {}).get("S1"), game.get("SC", {}).get("FS", {}).get("S2")],
            "candidates": candidates[:5],
        }, ensure_ascii=False, default=str))
        printed += 1
        if printed >= 3:
            break

    if printed == 0:
        raise SystemExit("No live event exposed a non-line T=1/2/3 outcome group")


if __name__ == "__main__":
    main()
