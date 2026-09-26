from __future__ import annotations

from dataclasses import dataclass
from math import prod
from typing import Iterable


@dataclass(frozen=True)
class PrematchPick:
    event_id: str
    home: str
    away: str
    market: str
    selection: str
    odds: float
    model_probability: float
    market_probability: float
    data_quality: float = 1.0

    @property
    def edge(self) -> float:
        return self.model_probability - self.market_probability

    @property
    def expected_value(self) -> float:
        return self.model_probability * self.odds - 1.0


def devig_two_way(over_odds: float, under_odds: float) -> tuple[float, float]:
    a, b = 1.0 / over_odds, 1.0 / under_odds
    margin = a + b
    if margin <= 0:
        return 0.5, 0.5
    return a / margin, b / margin


def qualified_pick(
    pick: PrematchPick,
    *,
    min_probability: float = 0.64,
    min_edge: float = 0.04,
    min_ev: float = 0.02,
    min_quality: float = 0.60,
    min_odds: float = 1.20,
    max_odds: float = 2.40,
) -> bool:
    return (
        min_odds <= pick.odds <= max_odds
        and pick.model_probability >= min_probability
        and pick.edge >= min_edge
        and pick.expected_value >= min_ev
        and pick.data_quality >= min_quality
    )


def build_accumulators(
    picks: Iterable[PrematchPick],
    *,
    legs: int = 2,
    min_combined_probability: float = 0.42,
    min_combined_odds: float = 1.65,
    max_combined_odds: float = 4.00,
) -> list[dict]:
    pool = [p for p in picks if qualified_pick(p)]
    pool.sort(key=lambda p: (p.edge, p.expected_value, p.model_probability), reverse=True)
    out: list[dict] = []

    def walk(start: int, chosen: list[PrematchPick]) -> None:
        if len(chosen) == legs:
            if len({p.event_id for p in chosen}) != legs:
                return
            combined_p = prod(p.model_probability for p in chosen)
            combined_odds = prod(p.odds for p in chosen)
            if combined_p < min_combined_probability:
                return
            if not (min_combined_odds <= combined_odds <= max_combined_odds):
                return
            out.append({
                "legs": chosen.copy(),
                "combined_probability": combined_p,
                "combined_odds": combined_odds,
                "expected_value": combined_p * combined_odds - 1.0,
                "score": combined_p * (1.0 + sum(p.edge for p in chosen)),
            })
            return
        for i in range(start, len(pool)):
            candidate = pool[i]
            if any(candidate.event_id == p.event_id for p in chosen):
                continue
            walk(i + 1, [*chosen, candidate])

    walk(0, [])
    out.sort(key=lambda row: (row["score"], row["expected_value"]), reverse=True)
    return out


def picks_from_goal_profile(
    *,
    event_id: str,
    home: str,
    away: str,
    profile: dict,
    market: dict,
    data_quality: float = 1.0,
) -> list[PrematchPick]:
    """Convert existing GOOL prematch history + 1xBet snapshot into priced V4 picks.

    V4 intentionally starts with full-match totals. 1X2 needs a separate
    home/draw/away probability head; it must not be inferred from total goals.
    """
    first = profile.get("first_half") or {}
    second = profile.get("second_half") or {}
    if not first.get("available") or not second.get("available"):
        return []
    try:
        lam = float(first["expected_total"]) + float(second["expected_total"])
    except (TypeError, ValueError, KeyError):
        return []
    if lam <= 0:
        return []

    rows = market.get("match_totals") or []
    out: list[PrematchPick] = []
    import math
    for row in rows:
        try:
            line = float(row.get("line"))
            over_odd = float(row.get("over"))
            under_odd = float(row.get("under"))
        except (TypeError, ValueError):
            continue
        if over_odd <= 1 or under_odd <= 1:
            continue
        threshold = int(math.floor(line)) + 1
        cdf = sum(math.exp(-lam) * lam ** k / math.factorial(k) for k in range(threshold))
        model_over = max(0.0, min(1.0, 1.0 - cdf))
        fair_over, fair_under = devig_two_way(over_odd, under_odd)
        out.extend([
            PrematchPick(event_id, home, away, "match_total", f"over {line:g}", over_odd, model_over, fair_over, data_quality),
            PrematchPick(event_id, home, away, "match_total", f"under {line:g}", under_odd, 1.0 - model_over, fair_under, data_quality),
        ])
    return out
