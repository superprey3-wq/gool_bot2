from __future__ import annotations

from dataclasses import asdict
from statistics import median
from typing import Any

from .common import ProviderMatch
from .flashscore import FlashscoreProvider
from .fotmob import FotMobProvider
from .scores365 import Scores365Provider


class FootballDataFusion:
    """Build one provider-separated live record around Flashscore as source of truth."""

    def __init__(self) -> None:
        self.flashscore = FlashscoreProvider()
        self.fotmob = FotMobProvider()
        self.scores365 = Scores365Provider()

    @staticmethod
    def _consensus(matches: list[ProviderMatch], key: str) -> tuple[float | None, float | None]:
        """Robust per-side consensus across Flashscore, FotMob and 365Scores."""
        home_values: list[float] = []
        away_values: list[float] = []
        for match in matches:
            if key not in match.stats:
                continue
            home, away = match.stats[key]
            home_values.append(float(home))
            away_values.append(float(away))
        if not home_values:
            return None, None
        return round(float(median(home_values)), 4), round(float(median(away_values)), 4)

    @staticmethod
    def _spread(matches: list[ProviderMatch], key: str) -> tuple[float | None, float | None]:
        home_values: list[float] = []
        away_values: list[float] = []
        for match in matches:
            if key not in match.stats:
                continue
            home, away = match.stats[key]
            home_values.append(float(home))
            away_values.append(float(away))
        if len(home_values) < 2:
            return None, None
        return round(max(home_values) - min(home_values), 4), round(max(away_values) - min(away_values), 4)

    def enrich_flashscore_match(self, match: ProviderMatch) -> dict[str, Any]:
        fs = ProviderMatch(
            **{
                **asdict(match),
                "stats": self.flashscore.fetch_stats(match.provider_match_id),
                "meta": {
                    **match.meta,
                    "goal_timeline": self.flashscore.fetch_goal_timeline(match.provider_match_id),
                },
            }
        )
        providers: list[ProviderMatch] = [fs]
        fotmob = self.fotmob.enrich(match.home, match.away)
        if fotmob:
            providers.append(fotmob)
        scores365 = self.scores365.enrich(match.home, match.away)
        if scores365:
            providers.append(scores365)

        xg_consensus = self._consensus(providers, "xg")
        xgot_consensus = self._consensus(providers, "xgot")
        xg_spread = self._spread(providers, "xg")
        xgot_spread = self._spread(providers, "xgot")

        return {
            "match": {
                "flashscore_event_id": fs.provider_match_id,
                "home": fs.home,
                "away": fs.away,
                "league": fs.league,
                "minute": fs.minute,
                "home_score": fs.home_score,
                "away_score": fs.away_score,
                "is_halftime": fs.is_halftime,
            },
            "providers": {p.provider: {"id": p.provider_match_id, "stats": p.stats, "meta": p.meta} for p in providers},
            "consensus": {
                "xg": xg_consensus,
                "xgot": xgot_consensus,
                "xg_source_spread": xg_spread,
                "xgot_source_spread": xgot_spread,
                "provider_count": len(providers),
            },
        }

    def live_records(self) -> list[dict[str, Any]]:
        return [self.enrich_flashscore_match(match) for match in self.flashscore.live_matches()]
