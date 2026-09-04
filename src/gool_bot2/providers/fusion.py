from __future__ import annotations

from dataclasses import asdict
from datetime import datetime, timezone
from statistics import median
from typing import Any

from .common import ProviderMatch
from .flashscore import FlashscoreProvider
from .flashscore_incident_guard import goals_from_incidents
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

    @staticmethod
    def _timeline_final_score(timeline: list[dict[str, Any]]) -> tuple[int, int]:
        home = away = 0
        for row in timeline:
            score = row.get("score") or []
            try:
                if len(score) >= 2:
                    home = max(home, int(score[0] or 0))
                    away = max(away, int(score[1] or 0))
            except (TypeError, ValueError):
                continue
        return home, away

    @classmethod
    def _select_goal_timeline(
        cls,
        providers: list[ProviderMatch],
        expected_score: tuple[int, int],
    ) -> tuple[list[dict[str, Any]], str, dict[str, Any]]:
        """Choose the timeline that best explains the current Flashscore score.

        Flashscore remains the score source of truth, but its incident endpoint can
        lag or change grammar independently. Prefer an exact provider timeline; if
        several are exact keep Flashscore first, then FotMob, then 365Scores. Never
        let a secondary source invent more goals than the current master score.
        """
        priority = {"flashscore": 0, "fotmob": 1, "365scores": 2}
        expected_total = max(0, int(expected_score[0])) + max(0, int(expected_score[1]))
        candidates: list[dict[str, Any]] = []
        for provider in providers:
            timeline = (provider.meta or {}).get("provider_goal_timeline")
            if timeline is None:
                timeline = (provider.meta or {}).get("goal_timeline")
            if not isinstance(timeline, list):
                timeline = []
            final_score = cls._timeline_final_score(timeline)
            total = final_score[0] + final_score[1]
            exact = final_score == expected_score
            safe = total <= expected_total
            candidates.append({
                "provider": provider.provider,
                "timeline": timeline,
                "final_score": final_score,
                "goal_count": len(timeline),
                "exact": exact,
                "safe": safe,
                "priority": priority.get(provider.provider, 99),
            })

        exact_rows = [row for row in candidates if row["exact"]]
        if exact_rows:
            best = min(exact_rows, key=lambda row: int(row["priority"]))
        else:
            safe_rows = [row for row in candidates if row["safe"]]
            if not safe_rows:
                best = min(candidates, key=lambda row: int(row["priority"])) if candidates else {
                    "provider": "none", "timeline": [], "final_score": (0, 0), "goal_count": 0
                }
            else:
                best = max(
                    safe_rows,
                    key=lambda row: (
                        int(row["final_score"][0]) + int(row["final_score"][1]),
                        -int(row["priority"]),
                    ),
                )

        audit = {
            row["provider"]: {
                "final_score": list(row["final_score"]),
                "goal_count": int(row["goal_count"]),
                "exact_score_match": bool(row["exact"]),
                "safe_vs_master": bool(row["safe"]),
            }
            for row in candidates
        }
        return list(best.get("timeline") or []), str(best.get("provider") or "none"), audit

    @staticmethod
    def _freshness_summary(providers: list[ProviderMatch]) -> dict[str, Any]:
        out: dict[str, Any] = {}
        for provider in providers:
            meta = provider.meta or {}
            endpoints = meta.get("endpoints") or {}
            out[provider.provider] = {
                "live_clock_seconds": meta.get("live_clock_seconds"),
                "endpoints": endpoints if isinstance(endpoints, dict) else {},
            }
        return out

    def enrich_flashscore_match(self, match: ProviderMatch) -> dict[str, Any]:
        observed_at = datetime.now(timezone.utc).isoformat()
        fs_stats = self.flashscore.fetch_stats(match.provider_match_id)
        try:
            fs_incidents = self.flashscore.fetch_incident_timeline(match.provider_match_id)
        except AttributeError:
            fs_incidents = []
        fs_goals = goals_from_incidents(fs_incidents) if fs_incidents else self.flashscore.fetch_goal_timeline(match.provider_match_id)
        fs = ProviderMatch(
            **{
                **asdict(match),
                "stats": fs_stats,
                "meta": {
                    **match.meta,
                    "provider_goal_timeline": fs_goals,
                    "incident_timeline": fs_incidents,
                    "endpoints": {
                        **dict((match.meta or {}).get("endpoints") or {}),
                        "master": {
                            "observed_at": observed_at,
                            "minute": match.minute,
                            "score": [match.home_score, match.away_score],
                            "status_code": (match.meta or {}).get("status_code"),
                        },
                        "stats": {
                            "observed_at": observed_at,
                            "stat_count": len(fs_stats),
                        },
                        "summary_incidents": {
                            "observed_at": observed_at,
                            "incident_count": len(fs_incidents),
                            "goal_count": len(fs_goals),
                        },
                    },
                },
            }
        )
        providers: list[ProviderMatch] = [fs]
        fotmob = self.fotmob.enrich(match.home, match.away)
        if fotmob:
            fotmob = ProviderMatch(**{**asdict(fotmob), "meta": {**fotmob.meta, "provider_goal_timeline": (fotmob.meta or {}).get("goal_timeline") or []}})
            providers.append(fotmob)
        scores365 = self.scores365.enrich(match.home, match.away)
        if scores365:
            scores365 = ProviderMatch(**{**asdict(scores365), "meta": {**scores365.meta, "provider_goal_timeline": (scores365.meta or {}).get("goal_timeline") or []}})
            providers.append(scores365)

        expected_score = (int(fs.home_score or 0), int(fs.away_score or 0))
        goal_timeline, goal_timeline_source, timeline_audit = self._select_goal_timeline(providers, expected_score)
        fs = ProviderMatch(**{
            **asdict(fs),
            "meta": {
                **fs.meta,
                "goal_timeline": goal_timeline,
                "goal_timeline_source": goal_timeline_source,
                "goal_timeline_candidates": timeline_audit,
            },
        })
        providers[0] = fs

        xg_consensus = self._consensus(providers, "xg")
        xgot_consensus = self._consensus(providers, "xgot")
        xg_spread = self._spread(providers, "xg")
        xgot_spread = self._spread(providers, "xgot")

        return {
            "captured_at": observed_at,
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
            "provider_freshness": self._freshness_summary(providers),
            "consensus": {
                "xg": xg_consensus,
                "xgot": xgot_consensus,
                "xg_source_spread": xg_spread,
                "xgot_source_spread": xgot_spread,
                "provider_count": len(providers),
                "goal_timeline_source": goal_timeline_source,
            },
        }

    def live_records(self) -> list[dict[str, Any]]:
        return [self.enrich_flashscore_match(match) for match in self.flashscore.live_matches()]
