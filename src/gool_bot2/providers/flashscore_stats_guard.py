from __future__ import annotations

import re

from .flashscore import FlashscoreProvider, STAT_MAP, _fields, _to_number


_STATS_RE = re.compile(r"SD(?:÷|¬)(\d+).*?SH(?:÷|¬)([^¬~]+).*?SI(?:÷|¬)([^¬~]+)")


def _section_priority(section: str) -> int:
    """Prefer full-match cumulative stats over half/period splits."""
    normalized = str(section or "").strip().casefold()
    if normalized in {"match", "overall", "full match", "total", "all"}:
        return 3
    if "match" in normalized and "half" not in normalized:
        return 3
    if not normalized:
        # Older/partial feeds without SE headers are treated as cumulative, but
        # equal-priority duplicates never overwrite the first value we saw.
        return 2
    return 1


def parse_match_stats_body(body: str) -> dict[str, tuple[float, float]]:
    """Parse Flashscore stats while keeping the cumulative Match section.

    Flashscore can emit the same stat IDs in `Match`, `1st Half` and `2nd Half`
    sections. The previous parser overwrote Match totals with the last period
    section, which could turn e.g. 15 cumulative shots into one second-half shot.
    """
    out: dict[str, tuple[float, float]] = {}
    priorities: dict[str, int] = {}
    section = ""

    for chunk in (body or "").split("~"):
        if not chunk:
            continue
        fields = _fields(chunk)
        if "SE" in fields:
            section = str(fields.get("SE") or "").strip()

        match = _STATS_RE.search(chunk)
        if not match:
            continue
        stat_id, home, away = match.groups()
        name = STAT_MAP.get(stat_id)
        if not name:
            continue

        priority = _section_priority(section)
        if name in out and priority <= priorities.get(name, -1):
            continue
        out[name] = (_to_number(home), _to_number(away))
        priorities[name] = priority

    return out


def install() -> None:
    """Install the cumulative-stat parser on FlashscoreProvider once."""
    if getattr(FlashscoreProvider, "_match_stats_guard_installed", False):
        return

    def fetch_stats(self: FlashscoreProvider, event_id: str) -> dict[str, tuple[float, float]]:
        body = self._feed(f"df_st_1_{event_id}")
        return parse_match_stats_body(body)

    FlashscoreProvider.fetch_stats = fetch_stats
    FlashscoreProvider._match_stats_guard_installed = True
