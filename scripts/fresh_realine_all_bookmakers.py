from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path


def load(path: Path) -> list[dict]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        return value if isinstance(value, list) else []
    except Exception:
        return []


async def main_async(matches_path: Path, source_dir: Path, output: Path, limit: int) -> None:
    sys.path.insert(0, str(source_dir / "src"))
    from flashscore_bot.common_scraper import fetch_all_odds_data
    from flashscore_bot.config import BOOKMAKER_MAPPING

    bookmakers = list(BOOKMAKER_MAPPING)
    bet_types = {"over-under": True, "both-teams-to-score": True}
    rows: list[dict] = []
    print(f"REALINE_ALL bookmakers={bookmakers}")
    for match in load(matches_path)[:limit]:
        row = {"source": "realine0/flashscore-football-odds-scraper", "match": match}
        try:
            odds = await fetch_all_odds_data(str(match.get("event_id") or ""), bookmakers, bet_types)
            row.update(
                {
                    "ok": bool(odds),
                    "source_ok": bool(odds),
                    "odds_fields": len(odds),
                    "payload": odds,
                    "bookmakers_requested": bookmakers,
                }
            )
        except Exception as exc:
            row.update({"ok": False, "source_ok": False, "error": f"{type(exc).__name__}: {exc}"})
        rows.append(row)
        print(
            f"REALINE_ALL match={match.get('home')} - {match.get('away')} "
            f"ok={row.get('ok')} fields={row.get('odds_fields', 0)}"
        )
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--matches", type=Path, required=True)
    parser.add_argument("--source-dir", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--limit", type=int, default=8)
    args = parser.parse_args()
    asyncio.run(main_async(args.matches, args.source_dir, args.output, args.limit))


if __name__ == "__main__":
    main()
