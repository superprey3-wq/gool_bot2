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
