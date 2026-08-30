from __future__ import annotations

import argparse
import json
import re
import time
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

from .archive_training import ArchiveGoalEvent, ArchiveMatch
from .providers.flashscore import FlashscoreProvider, _fields
from .providers.common import UA, http_text

FINISHED_COARSE_STATUS = "3"


@dataclass(frozen=True)
class SeasonSeed:
    tournament_id: str
    season_id: str
    league: str = ""


@dataclass(frozen=True)
class HistoricalMatchRef:
    match_id: str
    kickoff_at: datetime
    home: str
    away: str
    league: str
    home_score: int
    away_score: int


def _as_int(value: object, default: int = 0) -> int:
    try:
        return int(float(str(value)))
    except (TypeError, ValueError):
        return default


def discover_seed_from_results_url(url: str, timeout: int = 20) -> SeasonSeed:
    """Discover Flashscore tournament/season ids from a league results page.

    This is intentionally a convenience helper. Flashscore frontend markup can
    change, so production backfills should persist discovered ids in a seed file.
    """
    headers = {"User-Agent": UA, "Accept": "text/html,*/*"}
    code, body = http_text(url, headers=headers, timeout=timeout)
    if code != 200 or not body:
        raise RuntimeError(f"Unable to load results page: HTTP {code}")

    tournament_patterns = (
        r'"tournament_id"\s*:\s*"([^"]+)"',
        r"tournament_id\s*[=:]\s*['\"]([^'\"]+)['\"]",
    )
    season_patterns = (
        r'"season_id"\s*:\s*"([^"]+)"',
        r"season_id\s*[=:]\s*['\"]([^'\"]+)['\"]",
    )

    def first(patterns: tuple[str, ...]) -> str:
        for pattern in patterns:
            match = re.search(pattern, body, re.I)
            if match:
                return match.group(1)
        return ""

    tournament_id = first(tournament_patterns)
    season_id = first(season_patterns)
    if not tournament_id or not season_id:
        raise RuntimeError("Could not discover tournament_id/season_id from page")
    return SeasonSeed(tournament_id=tournament_id, season_id=season_id)


def parse_finished_season_feed(body: str, fallback_league: str = "") -> list[HistoricalMatchRef]:
    """Parse finished matches from a Flashscore tournament-season feed."""
    refs: list[HistoricalMatchRef] = []
    league = fallback_league
    for chunk in (body or "").split("~"):
        if not chunk:
            continue
        if chunk.startswith("ZA÷"):
            league = _fields(chunk).get("ZA", league).strip() or league
            continue
        if not chunk.startswith("AA÷"):
            continue

        event_id, sep, rest = chunk[3:].partition("¬")
        if not sep or not event_id:
            continue
        f = _fields(rest)
        if f.get("AB") != FINISHED_COARSE_STATUS:
            continue

        home = (f.get("AE") or f.get("CX") or "").strip()
        away = (f.get("AF") or "").strip()
        if not home or not away:
            continue

        kickoff_ts = _as_int(f.get("AD"), 0)
        if kickoff_ts <= 0:
            continue
        kickoff_at = datetime.fromtimestamp(kickoff_ts, tz=timezone.utc)
        refs.append(
            HistoricalMatchRef(
                match_id=event_id,
                kickoff_at=kickoff_at,
                home=home,
                away=away,
                league=league,
                home_score=_as_int(f.get("AG"), _as_int(f.get("AT"))),
                away_score=_as_int(f.get("AH"), _as_int(f.get("AU"))),
            )
        )
    return list({m.match_id: m for m in refs}.values())


class FlashscoreArchiveBackfill:
    """Slow, append-friendly historical backfill built on the validated feed adapter."""

    def __init__(self, provider: FlashscoreProvider | None = None, delay_seconds: float = 2.2):
        self.provider = provider or FlashscoreProvider()
        self.delay_seconds = max(0.0, float(delay_seconds))

    def season_matches(self, seed: SeasonSeed) -> list[HistoricalMatchRef]:
        path = f"tr_1_{seed.tournament_id}_{seed.season_id}_0_2_en_1"
        body = self.provider._feed(path)
        if not body:
            raise RuntimeError(f"Empty Flashscore season feed for {seed}")
        return parse_finished_season_feed(body, fallback_league=seed.league)

    def fetch_archive_match(self, ref: HistoricalMatchRef) -> ArchiveMatch:
        timeline = self.provider.fetch_goal_timeline(ref.match_id)
        goals: list[ArchiveGoalEvent] = []
        for item in timeline:
            minute = float(item["minute"])
            goals.append(
                ArchiveGoalEvent(
                    minute=minute,
                    period=1 if minute <= 45 else 2,
                    side=str(item["side"]),
                )
            )
        return ArchiveMatch(match_id=ref.match_id, kickoff_at=ref.kickoff_at, goals=tuple(goals))

    def iter_season(self, seed: SeasonSeed) -> Iterable[tuple[HistoricalMatchRef, ArchiveMatch]]:
        for ref in self.season_matches(seed):
            archive_match = self.fetch_archive_match(ref)
            yield ref, archive_match
            if self.delay_seconds:
                time.sleep(self.delay_seconds)


def _serialize(ref: HistoricalMatchRef, match: ArchiveMatch, seed: SeasonSeed) -> dict[str, object]:
    return {
        "match_id": ref.match_id,
        "kickoff_at": ref.kickoff_at.isoformat(),
        "home": ref.home,
        "away": ref.away,
        "league": ref.league or seed.league,
        "final_score": [ref.home_score, ref.away_score],
        "tournament_id": seed.tournament_id,
        "season_id": seed.season_id,
        "goals": [asdict(goal) for goal in match.goals],
    }


def backfill_to_jsonl(seed: SeasonSeed, output: Path, delay_seconds: float = 2.2, limit: int | None = None) -> int:
    """Backfill one season into append-only JSONL and return saved match count."""
    output.parent.mkdir(parents=True, exist_ok=True)
    existing: set[str] = set()
    if output.exists():
        for line in output.read_text(encoding="utf-8").splitlines():
            try:
                existing.add(str(json.loads(line)["match_id"]))
            except (json.JSONDecodeError, KeyError, TypeError):
                continue

    backfill = FlashscoreArchiveBackfill(delay_seconds=delay_seconds)
    saved = 0
    with output.open("a", encoding="utf-8") as handle:
        for ref, match in backfill.iter_season(seed):
            if ref.match_id in existing:
                continue
            handle.write(json.dumps(_serialize(ref, match, seed), ensure_ascii=False) + "\n")
            handle.flush()
            existing.add(ref.match_id)
            saved += 1
            if limit is not None and saved >= limit:
                break
    return saved


def main() -> None:
    parser = argparse.ArgumentParser(description="Backfill finished Flashscore matches for GOOL training")
    parser.add_argument("--tournament-id")
    parser.add_argument("--season-id")
    parser.add_argument("--league", default="")
    parser.add_argument("--results-url", default="")
    parser.add_argument("--output", default="data/raw/flashscore_archive.jsonl")
    parser.add_argument("--delay", type=float, default=2.2)
    parser.add_argument("--limit", type=int, default=None)
    args = parser.parse_args()

    if args.results_url:
        seed = discover_seed_from_results_url(args.results_url)
        if args.league:
            seed = SeasonSeed(seed.tournament_id, seed.season_id, args.league)
    elif args.tournament_id and args.season_id:
        seed = SeasonSeed(args.tournament_id, args.season_id, args.league)
    else:
        parser.error("Provide --results-url or both --tournament-id and --season-id")

    saved = backfill_to_jsonl(seed, Path(args.output), delay_seconds=args.delay, limit=args.limit)
    print(f"saved_matches={saved} output={args.output}")


if __name__ == "__main__":
    main()
