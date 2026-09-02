from __future__ import annotations

import argparse
import asyncio
import json
import re
import sys
import unicodedata
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any


def _norm(value: str) -> str:
    text = unicodedata.normalize("NFKD", str(value or "")).encode("ascii", "ignore").decode().lower()
    text = re.sub(r"\b(fc|afc|cf|sc|fk|sv|ac|as|u19|u20|u21|u23)\b", " ", text)
    text = re.sub(r"[^a-z0-9]+", " ", text)
    return " ".join(text.split())


def _sim(a: str, b: str) -> float:
    a, b = _norm(a), _norm(b)
    if not a or not b:
        return 0.0
    if a == b:
        return 1.0
    if a in b or b in a:
        return 0.93
    return SequenceMatcher(None, a, b).ratio()


def _pair(home: str, away: str, candidate: dict[str, Any]) -> float:
    ch = str(candidate.get("home_team") or "")
    ca = str(candidate.get("away_team") or "")
    direct = (_sim(home, ch) + _sim(away, ca)) / 2.0
    reverse = (_sim(home, ca) + _sim(away, ch)) / 2.0
    return max(direct, reverse)


def _market_summary(event: dict[str, Any]) -> dict[str, Any]:
    books = event.get("bookmakers") or []
    markets = []
    for book in books:
        for market in book.get("markets") or []:
            outcomes = [o for o in (market.get("outcomes") or []) if o.get("price") is not None and o.get("active", True)]
            if outcomes:
                markets.append({"key": market.get("key"), "name": market.get("name"), "outcomes": outcomes})
    totals = [m for m in markets if "total" in str(m.get("key") or "").lower() or "total" in str(m.get("name") or "").lower()]
    team_totals = [m for m in totals if "team" in str(m.get("key") or "").lower() or "team" in str(m.get("name") or "").lower()]
    return {"market_count": len(markets), "totals": totals, "team_totals": team_totals, "all_markets": markets}


async def _scrape(source_dir: Path) -> tuple[list[dict[str, Any]], list[str]]:
    sys.path.insert(0, str(source_dir.resolve()))
    from bookie_scraper import ScrapeConfig, run

    results = await run(ScrapeConfig(bookmakers=["pinnacle"], sports=["soccer"], depth="full", output_dir=None))
    events: list[dict[str, Any]] = []
    errors: list[str] = []
    for result in results:
        errors.extend(list(getattr(result, "errors", []) or []))
        for event in getattr(result, "events", []) or []:
            payload = event.to_odds_api()
            if str(payload.get("status") or "").upper() == "LIVE":
                events.append(payload)
    return events, errors


def main() -> int:
    parser = argparse.ArgumentParser(description="Test today's BookieScraper Pinnacle guest API against all current Flashscore live matches")
    parser.add_argument("--matches", type=Path, required=True)
    parser.add_argument("--source-dir", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--threshold", type=float, default=0.72)
    args = parser.parse_args()

    matches = json.loads(args.matches.read_text(encoding="utf-8"))
    events, errors = asyncio.run(_scrape(args.source_dir))

    out_rows = []
    matched = 0
    matched_totals = 0
    matched_team_totals = 0
    for match in matches:
        home, away = str(match.get("home") or ""), str(match.get("away") or "")
        best = None
        best_score = 0.0
        for event in events:
            score = _pair(home, away, event)
            if score > best_score:
                best_score, best = score, event
        row: dict[str, Any] = {"match": match, "similarity": round(best_score, 4)}
        if best is None or best_score < args.threshold:
            row.update({"matched": False, "has_totals": False, "has_team_totals": False})
        else:
            matched += 1
            market_info = _market_summary(best)
            has_totals = bool(market_info["totals"])
            has_team_totals = bool(market_info["team_totals"])
            matched_totals += int(has_totals)
            matched_team_totals += int(has_team_totals)
            row.update(
                {
                    "matched": True,
                    "has_totals": has_totals,
                    "has_team_totals": has_team_totals,
                    "bookie_event": best,
                    "market_info": market_info,
                }
            )
        out_rows.append(row)
        print(f"BOOKIESCRAPER {match.get('minute', 0)}' {home} - {away}: matched={row['matched']} totals={row.get('has_totals', False)}")

    summary = {
        "flashscore_live_tested": len(matches),
        "pinnacle_live_events_from_source": len(events),
        "matched_flashscore_live": matched,
        "matched_with_totals": matched_totals,
        "matched_with_team_totals": matched_team_totals,
        "match_coverage": matched / len(matches) if matches else 0.0,
        "totals_coverage": matched_totals / len(matches) if matches else 0.0,
        "source_errors": errors,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps({"summary": summary, "matches": out_rows}, ensure_ascii=False, indent=2), encoding="utf-8")
    print("BOOKIESCRAPER_PINNACLE_ALL_LIVE_SUMMARY")
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
