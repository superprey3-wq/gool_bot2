"""One centrally enforced minimum price for public GOOL standalone bets.

The 1.70 floor applies to PREMATCH and LIVE singles in all three sports.
Parlay legs are intentionally *not* passed through this policy.
Existing environment overrides may raise the floor, never lower it.
"""
from __future__ import annotations

import math
import os
from typing import Any


PUBLIC_SINGLE_FLOOR = 1.70


def _env_float(name: str, fallback: float) -> float:
    try:
        value = float(os.getenv(name, str(fallback)))
    except (ValueError, TypeError):
        return fallback
    return value if math.isfinite(value) else fallback


def minimum_single_odd(*, phase: str = "LIVE", sport: str = "football") -> float:
    phase = str(phase or "LIVE").strip().upper()
    sport = str(sport or "football").strip().lower()
    minimum = max(PUBLIC_SINGLE_FLOOR, _env_float("GOOL_SINGLE_MIN_ODD", PUBLIC_SINGLE_FLOOR))
    if phase == "PREMATCH":
        minimum = max(minimum, _env_float("GOOL_PREMATCH_SINGLE_MIN_ODD", PUBLIC_SINGLE_FLOOR))
        if sport in {"hockey", "basketball"}:
            minimum = max(minimum, _env_float("GOOL_MULTISPORT_SINGLE_MIN_ODD", PUBLIC_SINGLE_FLOOR))
    else:
        minimum = max(minimum, _env_float("GOOL_LIVE_SINGLE_MIN_ODD", PUBLIC_SINGLE_FLOOR))
    if sport == "basketball":
        minimum = max(minimum, _env_float("GOOL_BASKETBALL_SINGLE_MIN_ODD", PUBLIC_SINGLE_FLOOR))
    if sport == "hockey":
        minimum = max(minimum, _env_float("GOOL_HOCKEY_SINGLE_MIN_ODD", PUBLIC_SINGLE_FLOOR))
    return minimum


def public_single_allowed(odd: Any, *, phase: str = "LIVE", sport: str = "football") -> bool:
    try:
        price = float(odd)
    except (ValueError, TypeError):
        return False
    return math.isfinite(price) and price >= minimum_single_odd(phase=phase, sport=sport)


def public_single_rejection(odd: Any, *, phase: str = "LIVE", sport: str = "football") -> str | None:
    if public_single_allowed(odd, phase=phase, sport=sport):
        return None
    floor = minimum_single_odd(phase=phase, sport=sport)
    return f"public_single_odd_below_{floor:.2f}_or_missing"


__all__ = ["PUBLIC_SINGLE_FLOOR", "minimum_single_odd", "public_single_allowed", "public_single_rejection"]
