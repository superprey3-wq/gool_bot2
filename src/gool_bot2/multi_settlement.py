from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .multi_router import MarketCandidate, RouterDecision


@dataclass(slots=True)
class CandidateSettlement:
    key: str
    label: str
    result: str
    profit_units: float

    def to_dict(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "label": self.label,
            "result": self.result,
            "profit_units": round(float(self.profit_units), 4),
        }


def _line_from_key(candidate: MarketCandidate) -> float | None:
    if ":" not in candidate.key:
        return None
    try:
        return float(candidate.key.rsplit(":", 1)[1])
    except (TypeError, ValueError):
        return None


def settle_candidate(candidate: MarketCandidate, final_score: tuple[int, int]) -> CandidateSettlement:
    hs, aws = int(final_score[0]), int(final_score[1])
    result = "void"
    profit = 0.0

    if candidate.family in {"match_total", "asian_match_total"}:
        line = _line_from_key(candidate)
        if line is not None:
            total = hs + aws
            if total > line:
                result = "won"
                profit = candidate.odd - 1.0
            elif abs(total - line) < 1e-9:
                result = "push"
                profit = 0.0
            else:
                result = "lost"
                profit = -1.0

    elif candidate.family == "team_total":
        line = _line_from_key(candidate)
        if line is not None:
            goals = hs if candidate.key.startswith("home_total:") else aws
            if goals > line:
                result = "won"
                profit = candidate.odd - 1.0
            elif abs(goals - line) < 1e-9:
                result = "push"
                profit = 0.0
            else:
                result = "lost"
                profit = -1.0

    elif candidate.family == "btts":
        if hs > 0 and aws > 0:
            result = "won"
            profit = candidate.odd - 1.0
        else:
            result = "lost"
            profit = -1.0

    return CandidateSettlement(candidate.key, candidate.label, result, profit)


def settle_decision(decision: RouterDecision, final_score: tuple[int, int]) -> dict[str, Any]:
    winner = None if decision.winner is None else settle_candidate(decision.winner, final_score)
    alternatives = [settle_candidate(row, final_score) for row in decision.alternatives]
    best_realized = max(
        ([winner] if winner is not None else []) + alternatives,
        key=lambda row: row.profit_units,
        default=None,
    )
    return {
        "final_score": [int(final_score[0]), int(final_score[1])],
        "winner": None if winner is None else winner.to_dict(),
        "alternatives": [row.to_dict() for row in alternatives],
        "best_realized": None if best_realized is None else best_realized.to_dict(),
        "router_picked_best_realized": bool(
            winner is not None and best_realized is not None and winner.profit_units >= best_realized.profit_units
        ),
    }
