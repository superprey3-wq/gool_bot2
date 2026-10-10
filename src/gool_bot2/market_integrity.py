"""Minimal fail-closed market identity checks before quoting a bet.

This is NOT a probability model. Implausible prices/lines must not be
silently reinterpreted as points markets. Keep the raw quote for diagnosis.
"""
from __future__ import annotations

import math
from typing import Any


def basketball_points_market_anomaly(lane: dict[str, Any]) -> str | None:
    family = str(lane.get("market_family") or "")
    scope = str(lane.get("scope") or "")
    if family not in {"match_total", "home_total", "away_total"}:
        return None
    try:
        line = float(lane.get("line"))
    except (TypeError, ValueError):
        return "missing_points_line"
    if not math.isfinite(line) or line <= 0:
        return "invalid_points_line"

    # Only reject orders of magnitude incompatible with ordinary 5-on-5
    # basketball points. Preserve legitimate low-scoring youth/women's games.
    # A QUARTER_4 team-total 2.5 at ~2.4 odds is more likely a misdecoded
    # 3-point-field-goals market than ordinary team points.
    team = family != "match_total"
    if scope.startswith("QUARTER_"):
        if line < (4.0 if team else 10.0):
            return "implausibly_low_quarter_points"
        if line > (85.0 if team else 160.0):
            return "implausibly_high_quarter_points"
    elif scope in {"FIRST_HALF", "SECOND_HALF"}:
        if line < (9.0 if team else 20.0):
            return "implausibly_low_half_points"
    elif scope == "FULL_MATCH":
        if line < (18.0 if team else 40.0):
            return "implausibly_low_full_match_points"
    return None


def bookmaker_kickoff_compatible(
    bookmaker: dict[str, Any],
    flashscore: dict[str, Any],
    *,
    tolerance_seconds: float = 4 * 3600,
) -> bool:
    """Never map repeat team names across different fixture days.

    Missing timestamps preserve legacy matching (data quality is tracked
    separately). Known incompatible UTC start times fail closed.
    """
    def timestamp(value: Any) -> float | None:
        try:
            ts = float(value)
        except (ValueError, TypeError):
            return None
        if ts > 1e12:
            ts /= 1000.0
        if not math.isfinite(ts) or ts < 1_000_000_000:
            return None
        return ts

    book = timestamp(bookmaker.get("S") or bookmaker.get("start_ts"))
    fs = timestamp(flashscore.get("match_start_ts") or flashscore.get("start_ts"))
    if book is None or fs is None:
        return True
    return abs(book - fs) <= tolerance_seconds
