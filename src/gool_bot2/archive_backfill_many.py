from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

from .archive_flashscore import SeasonSeed, backfill_to_jsonl, discover_seed_from_results_url


def _load_seeds(path: Path) -> list[SeasonSeed]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, list):
        raise ValueError("Seed file must contain a JSON list")

    seeds: list[SeasonSeed] = []
    for item in payload:
        if not isinstance(item, dict):
            continue
        league = str(item.get("league") or "")
        results_url = str(item.get("results_url") or "")
        if results_url:
            seed = discover_seed_from_results_url(results_url)
            seeds.append(SeasonSeed(seed.tournament_id, seed.season_id, league))
            continue
        tournament_id = str(item.get("tournament_id") or "")
        season_id = str(item.get("season_id") or "")
        if tournament_id and season_id:
            seeds.append(SeasonSeed(tournament_id, season_id, league))
    return seeds


def main() -> None:
    parser = argparse.ArgumentParser(description="Resumable multi-season Flashscore historical backfill")
    parser.add_argument("--seeds", default="config/archive_seeds.json")
    parser.add_argument("--output", default=os.getenv("FLASHSCORE_ARCHIVE_FILE", "data/raw/flashscore_archive.jsonl"))
    parser.add_argument("--delay", type=float, default=float(os.getenv("FLASHSCORE_ARCHIVE_DELAY_SECONDS", "2.2")))
    parser.add_argument("--limit-per-season", type=int, default=None)
    args = parser.parse_args()

    seeds = _load_seeds(Path(args.seeds))
    if not seeds:
        raise SystemExit("No valid archive seeds found")

    total = 0
    for index, seed in enumerate(seeds, start=1):
        print(f"[{index}/{len(seeds)}] {seed.league or seed.tournament_id} season={seed.season_id}", flush=True)
        try:
            saved = backfill_to_jsonl(
                seed,
                Path(args.output),
                delay_seconds=args.delay,
                limit=args.limit_per_season,
            )
            total += saved
            print(f"  saved={saved} total={total}", flush=True)
        except Exception as exc:
            # One broken season/feed must not terminate a long archive run.
            print(f"  ERROR {type(exc).__name__}: {exc}", flush=True)
    print(f"done saved_matches={total} output={args.output}")


if __name__ == "__main__":
    main()
