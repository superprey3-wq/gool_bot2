from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict
from typing import Any

from .providers.common import ProviderMatch, pair_score
from .providers.flashscore import FlashscoreProvider
from .providers.fotmob import FotMobProvider
from .providers.scores365 import Scores365Provider


def find_flashscore_match(
    home: str,
    away: str,
    provider: FlashscoreProvider | None = None,
    threshold: float = 0.62,
) -> tuple[ProviderMatch | None, float]:
    """Find a LIVE match from the same Flashscore master feeds used by old GOOL."""
    provider = provider or FlashscoreProvider()
    best: ProviderMatch | None = None
    best_score = 0.0
    for match in provider.live_matches():
        score = pair_score(home, away, match.home, match.away)
        if score > best_score:
            best, best_score = match, score
    if best is None or best_score < threshold:
        return None, best_score
    return best, best_score


def probe_match(home: str, away: str) -> dict[str, Any]:
    """Probe Flashscore -> FotMob -> 365Scores for one currently LIVE match.

    Flashscore is the identity/source-of-truth anchor. Secondary providers are
    attached only after the Flashscore match has been found, preventing a fuzzy
    match at FotMob/365Scores from creating a match on its own.
    """
    flashscore = FlashscoreProvider()
    fotmob = FotMobProvider()
    scores365 = Scores365Provider()

    match, fs_score = find_flashscore_match(home, away, provider=flashscore)
    if match is None:
        return {
            "ok": False,
            "query": {"home": home, "away": away},
            "error": "flashscore_live_match_not_found",
            "flashscore_match_score": round(fs_score, 3),
        }

    fs_stats = flashscore.fetch_stats(match.provider_match_id)
    goals = flashscore.fetch_goal_timeline(match.provider_match_id)
    fotmob_match = fotmob.enrich(match.home, match.away)
    scores365_match = scores365.enrich(match.home, match.away)

    return {
        "ok": True,
        "query": {"home": home, "away": away},
        "match": {
            "event_id": match.provider_match_id,
            "home": match.home,
            "away": match.away,
            "league": match.league,
            "minute": match.minute,
            "score": [match.home_score, match.away_score],
            "is_halftime": match.is_halftime,
            "status_code": match.meta.get("status_code"),
            "match_score": round(fs_score, 3),
        },
        "flashscore": {
            "found": True,
            "stats": fs_stats,
            "goal_timeline": goals,
        },
        "fotmob": _provider_payload(fotmob_match),
        "365scores": _provider_payload(scores365_match),
    }


def _provider_payload(match: ProviderMatch | None) -> dict[str, Any]:
    if match is None:
        return {"found": False}
    data = asdict(match)
    return {
        "found": True,
        "provider_match_id": data["provider_match_id"],
        "stats": data["stats"],
        "meta": data["meta"],
    }


def _print_human(result: dict[str, Any]) -> None:
    if not result.get("ok"):
        print(
            "FLASHCORE: NOT FOUND "
            f"(best match score={result.get('flashscore_match_score', 0):.3f})"
        )
        return

    match = result["match"]
    print(
        "FLASHSCORE: FOUND "
        f"event_id={match['event_id']} | {match['home']} - {match['away']} | "
        f"{match['minute']}' | {match['score'][0]}:{match['score'][1]} | {match['league']}"
    )
    print("FLASHSCORE STATS:", json.dumps(result["flashscore"]["stats"], ensure_ascii=False))
    print("FLASHSCORE GOALS:", json.dumps(result["flashscore"]["goal_timeline"], ensure_ascii=False))

    for key, label in (("fotmob", "FOTMOB"), ("365scores", "365SCORES")):
        row = result[key]
        if not row.get("found"):
            print(f"{label}: NOT FOUND")
            continue
        print(
            f"{label}: FOUND id={row['provider_match_id']} | "
            f"stats={json.dumps(row['stats'], ensure_ascii=False)} | "
            f"meta={json.dumps(row['meta'], ensure_ascii=False)}"
        )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Probe one LIVE football match across GOOL data sources")
    parser.add_argument("home")
    parser.add_argument("away")
    parser.add_argument("--json", action="store_true", dest="as_json")
    args = parser.parse_args(argv)

    result = probe_match(args.home, args.away)
    if args.as_json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        _print_human(result)
    return 0 if result.get("ok") else 2


if __name__ == "__main__":
    sys.exit(main())
