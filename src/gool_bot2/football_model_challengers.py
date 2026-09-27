from __future__ import annotations

"""Shadow-only football model challengers.

These models never select or deliver a bet. They provide independent,
transparent probability estimates that can be logged beside the production
GOOL Brain and evaluated chronologically before any production influence.
"""

import math
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class ChallengerForecast:
    name: str
    home_lambda: float
    away_lambda: float
    home: float
    draw: float
    away: float

    @property
    def total_lambda(self) -> float:
        return self.home_lambda + self.away_lambda


def _num(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _poisson_1x2(home_lambda: float, away_lambda: float, max_goals: int = 10) -> tuple[float, float, float]:
    hp = [math.exp(-home_lambda) * home_lambda ** k / math.factorial(k) for k in range(max_goals + 1)]
    ap = [math.exp(-away_lambda) * away_lambda ** k / math.factorial(k) for k in range(max_goals + 1)]
    h = d = a = 0.0
    for hg, ph in enumerate(hp):
        for ag, pa in enumerate(ap):
            p = ph * pa
            if hg > ag:
                h += p
            elif hg == ag:
                d += p
            else:
                a += p
    mass = h + d + a
    return (h / mass, d / mass, a / mass) if mass else (1 / 3, 1 / 3, 1 / 3)


def poisson_profile_challenger(profile: dict[str, Any]) -> ChallengerForecast | None:
    """Independent Poisson baseline using GOOL's already-collected history.

    It deliberately consumes only pre-kickoff profile values, making it safe for
    chronological shadow evaluation and avoiding leakage from bookmaker odds.
    """
    full = profile.get("full_match") or {}
    hl = _num(full.get("home_expected_goals"))
    al = _num(full.get("away_expected_goals"))
    if hl is None or al is None or hl <= 0 or al <= 0:
        first = profile.get("first_half") or {}
        second = profile.get("second_half") or {}
        h1, a1 = _num(first.get("home_expected_goals")), _num(first.get("away_expected_goals"))
        h2, a2 = _num(second.get("home_expected_goals")), _num(second.get("away_expected_goals"))
        if None in (h1, a1, h2, a2):
            return None
        hl, al = float(h1 + h2), float(a1 + a2)
    # Keep sparse/noisy histories from creating absurd tails.
    hl = max(0.15, min(4.5, float(hl)))
    al = max(0.15, min(4.5, float(al)))
    h, d, a = _poisson_1x2(hl, al)
    return ChallengerForecast("poisson_profile_v1", hl, al, h, d, a)


def total_probability(forecast: ChallengerForecast, line: float, over: bool = True) -> float:
    lam = forecast.total_lambda
    threshold = int(math.floor(float(line))) + 1
    p_over = 1.0 - sum(math.exp(-lam) * lam ** k / math.factorial(k) for k in range(threshold))
    return p_over if over else 1.0 - p_over
