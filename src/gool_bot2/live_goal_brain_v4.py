from __future__ import annotations

import math
from dataclasses import dataclass, asdict
from typing import Any

from .match_context import provider_count, provider_pair, xg_or_proxy_pair


@dataclass(frozen=True)
class LiveGoalDecision:
    market: str
    decision: str
    probability: float
    confidence: float
    score: float
    reasons: tuple[str, ...]
    data_source: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _pair(record: dict[str, Any], key: str) -> tuple[float, float]:
    h, a = provider_pair(record, key)
    return float(h or 0.0), float(a or 0.0)


def _goal_probability(lam: float) -> float:
    return max(0.0, min(0.97, 1.0 - math.exp(-max(0.0, lam))))


def evaluate_live_goals(record: dict[str, Any]) -> list[LiveGoalDecision]:
    match = record.get("match") or {}
    minute = max(1, int(match.get("minute") or 1))
    if bool(match.get("is_finished")) or minute >= 90:
        return []

    xh, xa, xg_source, evidence = xg_or_proxy_pair(record)
    if xh is None or xa is None:
        return []
    xg = max(0.0, float(xh) + float(xa))
    shots = sum(_pair(record, "shots"))
    sot = sum(_pair(record, "shots_on_target"))
    big = sum(_pair(record, "big_chances"))
    box = sum(_pair(record, "touches_box"))
    corners = sum(_pair(record, "corners"))
    sources = provider_count(record)

    # Observed attacking rate, shrunk toward a neutral football prior.
    elapsed = max(8.0, float(minute))
    threat = xg + 0.035 * shots + 0.09 * sot + 0.22 * big + 0.008 * box + 0.012 * corners
    rate = threat / elapsed
    neutral_rate = 1.30 / 90.0
    reliability = min(1.0, 0.40 + 0.10 * min(evidence, 4) + 0.12 * min(sources, 3))
    blended_rate = reliability * rate + (1.0 - reliability) * neutral_rate

    decisions: list[LiveGoalDecision] = []
    windows = []
    if minute <= 42:
        windows.append(("GOAL_BEFORE_HT", max(0, 45 - minute), 0.58))
    if minute <= 82:
        windows.append(("ANOTHER_GOAL", max(0, 90 - minute), 0.64))

    for market, remaining, threshold in windows:
        lam = blended_rate * remaining
        probability = _goal_probability(lam)
        activity = min(1.0, (sot * 0.16 + big * 0.30 + xg * 0.22 + shots * 0.025))
        confidence = min(0.96, 0.42 + 0.14 * min(sources, 3) + 0.08 * min(evidence, 4) + 0.12 * activity)
        score = probability * confidence
        decision = "BET" if probability >= threshold and confidence >= 0.68 else "NO_BET"
        reasons = (
            f"minute={minute}",
            f"xg_or_proxy={xg:.2f}",
            f"shots={shots:.0f}",
            f"sot={sot:.0f}",
            f"big_chances={big:.0f}",
            f"sources={sources}",
            f"remaining={remaining}",
        )
        decisions.append(LiveGoalDecision(market, decision, round(probability, 4), round(confidence, 4), round(score, 4), reasons, xg_source))
    return decisions
