from __future__ import annotations

from typing import Any

from .common import ProviderMatch
from .fotmob import FotMobProvider
from .secondary_live_guard import FOTMOB_DETAIL_TTL_SECONDS, fotmob_live_clock_seconds


def _status(detail: dict[str, Any]) -> dict[str, Any]:
    header = detail.get("header") or {} if isinstance(detail, dict) else {}
    status = header.get("status") or {} if isinstance(header, dict) else {}
    return status if isinstance(status, dict) else {}


def install() -> None:
    """Prefer FotMob matchDetails clock over the slower daily-match cache.

    The daily matches list is cached for discovery and can lag by minutes. The
    matchDetails endpoint is already refreshed on a short TTL by
    secondary_live_guard, so use its header.status for timing/freshness metadata.
    """
    if getattr(FotMobProvider, "_fresh_detail_clock_installed", False):
        return

    original_enrich = FotMobProvider.enrich

    def enrich(self: FotMobProvider, home: str, away: str) -> ProviderMatch | None:
        result = original_enrich(self, home, away)
        if result is None:
            return None

        detail = self._detail(str(result.provider_match_id))
        status = _status(detail)
        meta = dict(result.meta or {})
        endpoints = dict(meta.get("endpoints") or {})
        match_details = dict(endpoints.get("match_details") or {})
        match_details.update({
            "cache_ttl_seconds": FOTMOB_DETAIL_TTL_SECONDS,
            "score": status.get("scoreStr"),
            "ongoing": status.get("ongoing"),
            "finished": status.get("finished"),
            "home_red_cards": status.get("numberOfHomeRedCards"),
            "away_red_cards": status.get("numberOfAwayRedCards"),
            "live_clock_seconds": fotmob_live_clock_seconds({"status": status}),
        })
        endpoints["match_details"] = match_details
        if "daily_match" in endpoints:
            daily_match = dict(endpoints.get("daily_match") or {})
            daily_match.setdefault("cache_ttl_seconds", 300.0)
            endpoints["daily_match"] = daily_match

        meta["live_clock_seconds"] = match_details.get("live_clock_seconds")
        meta["endpoints"] = endpoints
        return ProviderMatch(**{**result.__dict__, "meta": meta})

    FotMobProvider.enrich = enrich
    FotMobProvider._fresh_detail_clock_installed = True
