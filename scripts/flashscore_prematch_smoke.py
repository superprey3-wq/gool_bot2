from __future__ import annotations

import json
import os
import sys
from typing import Any

from gool_bot2.providers.flashscore import FlashscoreProvider


def _score_summary(rows: list[dict[str, Any]]) -> dict[str, Any]:
    totals = [int(r.get("home_score") or 0) + int(r.get("away_score") or 0) for r in rows]
    return {
        "n": len(rows),
        "totals": totals,
        "sum": sum(totals),
        "avg": round(sum(totals) / len(totals), 2) if totals else None,
        "all_zero": bool(totals) and all(v == 0 for v in totals),
        "sample": [
            {
                "id": r.get("event_id"),
                "home": r.get("home"),
                "away": r.get("away"),
                "score": [r.get("home_score"), r.get("away_score")],
                "section": r.get("section"),
                "timestamp": r.get("timestamp"),
            }
            for r in rows[:5]
        ],
    }


def main() -> int:
    provider = FlashscoreProvider()
    live = [m for m in provider.live_matches() if 15 <= int(m.minute or 0) <= 80]
    limit_matches = int(os.getenv("SMOKE_MATCHES", "8"))
    checked = 0
    failures: list[str] = []

    print(json.dumps({"live_candidates": len(live), "sample_limit": limit_matches}, ensure_ascii=False))
    for match in live[:limit_matches]:
        try:
            ctx = provider.fetch_match_history(match.provider_match_id, match.home, match.away, limit=10)
        except Exception as exc:
            failures.append(f"{match.home} - {match.away}: exception {type(exc).__name__}: {exc}")
            continue
        checked += 1
        home = _score_summary(list(ctx.get("home_recent") or []))
        away = _score_summary(list(ctx.get("away_recent") or []))
        payload = {
            "match": f"{match.home} - {match.away}",
            "id": match.provider_match_id,
            "minute": match.minute,
            "feed_present": ctx.get("feed_present"),
            "raw_matches": ctx.get("raw_matches"),
            "matched_home": ctx.get("matched_home"),
            "matched_away": ctx.get("matched_away"),
            "sections": ctx.get("section_counts"),
            "home": home,
            "away": away,
        }
        print("PREMATCH_SMOKE " + json.dumps(payload, ensure_ascii=False))

        raw = int(ctx.get("raw_matches") or 0)
        if ctx.get("feed_present") and raw >= 8:
            if home["n"] < 3 or away["n"] < 3:
                failures.append(f"{match.home} - {match.away}: feed has {raw} rows but matched home={home['n']} away={away['n']}")
            if home["n"] >= 5 and home["all_zero"]:
                failures.append(f"{match.home} - {match.away}: home recent parsed as all 0:0")
            if away["n"] >= 5 and away["all_zero"]:
                failures.append(f"{match.home} - {match.away}: away recent parsed as all 0:0")

    if checked == 0:
        print("No suitable live matches were available; smoke test is inconclusive.")
        return 0
    if failures:
        print("PREMATCH_SMOKE_FAILURES")
        for failure in failures:
            print("- " + failure)
        return 1
    print(f"PREMATCH_SMOKE_OK checked={checked}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
