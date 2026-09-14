from __future__ import annotations

import json
import urllib.parse
import urllib.request
from datetime import datetime, timezone


UA = "GOOL-matchbook-football-audit/1.0"


def main() -> int:
    params = urllib.parse.urlencode(
        {
            "tag-url-names": "soccer",
            "states": "open,suspended",
            "exchange-type": "back-lay",
            "odds-type": "DECIMAL",
            "include-prices": "true",
            "price-depth": 3,
            "price-mode": "expanded",
            "currency": "GBP",
            "minimum-liquidity": 2,
            "include-event-participants": "true",
            "markets-limit": 30,
            "per-page": 20,
        }
    )
    url = f"https://api.matchbook.com/edge/rest/events?{params}"
    print("GOOL MATCHBOOK FOOTBALL ORDERBOOK PROBE")
    print(f"captured_at={datetime.now(timezone.utc).isoformat()}")
    print(f"url={url}")
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=20) as response:
        payload = response.read()
        print(f"HTTP={response.status}")
        print(f"content_type={response.headers.get('Content-Type', '')}")
    data = json.loads(payload)
    events = list((data or {}).get("events") or [])
    print(f"football_events={len(events)}")

    market_words = ("match odds", "moneyline", "total", "over", "under", "goal", "1st half", "first half")
    for event in events[:12]:
        name = event.get("name")
        print("\nEVENT", json.dumps({
            "id": event.get("id"),
            "name": name,
            "start": event.get("start"),
            "status": event.get("status"),
            "in_running": event.get("in-running-flag"),
            "allow_live": event.get("allow-live-betting"),
            "volume": event.get("volume"),
        }, ensure_ascii=False))
        markets = list(event.get("markets") or [])
        chosen = [m for m in markets if any(word in str(m.get("name") or "").lower() for word in market_words)]
        if not chosen:
            chosen = markets[:6]
        for market in chosen[:10]:
            print(" MARKET", json.dumps({
                "id": market.get("id"),
                "name": market.get("name"),
                "status": market.get("status"),
                "volume": market.get("volume"),
                "in_running": market.get("in-running-flag"),
            }, ensure_ascii=False))
            for runner in list(market.get("runners") or [])[:8]:
                prices = []
                for price in list(runner.get("prices") or [])[:6]:
                    prices.append({
                        "side": price.get("side"),
                        "odds": price.get("odds"),
                        "available": price.get("available-amount"),
                    })
                print("  RUNNER", json.dumps({
                    "name": runner.get("name"),
                    "status": runner.get("status"),
                    "volume": runner.get("volume"),
                    "prices": prices,
                }, ensure_ascii=False))
    print("\nDONE")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
