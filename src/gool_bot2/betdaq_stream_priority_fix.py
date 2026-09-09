from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Callable

from . import betdaq_selection_matched as selection


_ORIGINAL_PRIORITY: Callable[..., list[int]] = selection.prioritize_stream_market_ids
_INSTALLED = False


def _utc(value: datetime | None) -> datetime:
    moment = value or datetime.now(timezone.utc)
    if moment.tzinfo is None:
        return moment.replace(tzinfo=timezone.utc)
    return moment.astimezone(timezone.utc)


def prioritize_fresh_stream_market_ids(
    event_rows: list[dict[str, Any]],
    markets: dict[int, dict[str, Any]],
    limit: int,
    *,
    now: datetime | None = None,
) -> list[int]:
    """Keep expired discovery rows from consuming BETDAQ stream capacity.

    Production discovery intentionally keeps a six-hour lookback so a genuinely
    LIVE event survives delayed hierarchy refreshes. The stream planner considers
    an event LIVE for up to four hours after kickoff. Rows older than that are no
    longer useful for Detailed Prices / Matched Amount subscriptions and must not
    be mixed into the planner's upcoming bucket.
    """
    moment = _utc(now)
    fresh: list[dict[str, Any]] = []
    for row in event_rows:
        start = selection._start_utc(row)
        if start is None:
            continue
        if start + timedelta(hours=4) < moment:
            continue
        fresh.append(row)
    return _ORIGINAL_PRIORITY(fresh, markets, limit, now=moment)


def install_betdaq_stream_priority_fix() -> None:
    global _INSTALLED
    if _INSTALLED:
        return
    selection.prioritize_stream_market_ids = prioritize_fresh_stream_market_ids
    _INSTALLED = True
    print("BETDAQ_STREAM_PRIORITY stale_events=excluded", flush=True)


__all__ = [
    "install_betdaq_stream_priority_fix",
    "prioritize_fresh_stream_market_ids",
]
