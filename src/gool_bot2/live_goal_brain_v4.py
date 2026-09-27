from __future__ import annotations

import math
from dataclasses import dataclass, asdict
from typing import Any

from .match_context import card_context, provider_count, provider_pair, xg_or_proxy_pair


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
    return max(0.0, min(0.95, 1.0 - math.exp(-max(0.0, lam))))


def evaluate_live_goals(record: dict[str, Any]) -> list[LiveGoalDecision]:
    match = record.get("match") or {}
    minute = max(1, int(match.get("minute") or 1))
    if bool(match.get("is_finished")) or minute >= 90 or bool(match.get("is_halftime")):
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
    score_total = max(0, int(match.get("home_score") or 0)) + max(0, int(match.get("away_score") or 0))
    cards = card_context(record)
    red_total = int(cards.get("home_red") or 0) + int(cards.get("away_red") or 0)

    elapsed = max(8.0, float(minute))
    threat = xg + 0.030 * shots + 0.075 * sot + 0.18 * big + 0.006 * box + 0.010 * corners
    rate = threat / elapsed
    neutral_rate = 1.25 / 90.0

    # Real provider xG deserves more trust than an attack proxy. Source count is
    # useful, but must not automatically push confidence to the ceiling.
    source_quality = 0.18 if xg_source == "provider_xg" else 0.08
    reliability = min(0.88, 0.30 + source_quality + 0.07 * min(evidence, 4) + 0.08 * min(sources, 3))
    blended_rate = reliability * rate + (1.0 - reliability) * neutral_rate

    # Score-state moderation: very high-scoring games can inflate cumulative
    # activity. Do not blindly extrapolate that rate late in the match.
    if score_total >= 4 and minute >= 55:
        blended_rate *= 0.88
    elif score_total >= 3 and minute >= 70:
        blended_rate *= 0.92

    # A red card changes match state sharply. Until V4 has post-card momentum
    # deltas, reduce certainty instead of assuming the card always helps goals.
    red_conf_penalty = 0.12 if red_total else 0.0

    decisions: list[LiveGoalDecision] = []
    windows: list[tuple[str, int, float]] = []
    if minute <= 42:
        windows.append(("GOAL_BEFORE_HT", max(0, 45 - minute), 0.62))
    if minute <= 82:
        windows.append(("ANOTHER_GOAL", max(0, 90 - minute), 0.68))

    for market, remaining, threshold in windows:
        lam = blended_rate * remaining
        probability = _goal_probability(lam)
        activity = min(1.0, sot * 0.12 + big * 0.22 + xg * 0.16 + shots * 0.018)
        confidence = (
            0.32
            + (0.16 if xg_source == "provider_xg" else 0.06)
            + 0.07 * min(sources, 3)
            + 0.045 * min(evidence, 4)
            + 0.10 * activity
            - red_conf_penalty
        )
        confidence = max(0.0, min(0.92, confidence))

        # Proxy-only decisions need a stronger probability margin because their
        # xG-like value is derived rather than supplied by a provider.
        effective_threshold = threshold + (0.05 if xg_source != "provider_xg" else 0.0)
        if red_total:
            effective_threshold += 0.03

        score = probability * confidence
        decision = "BET" if probability >= effective_threshold and confidence >= 0.70 else "NO_BET"
        reasons = (
            f"minute={minute}",
            f"score_total={score_total}",
            f"xg_or_proxy={xg:.2f}",
            f"shots={shots:.0f}",
            f"sot={sot:.0f}",
            f"big_chances={big:.0f}",
            f"sources={sources}",
            f"red_cards={red_total}",
            f"remaining={remaining}",
            f"threshold={effective_threshold:.2f}",
        )
        decisions.append(
            LiveGoalDecision(
                market,
                decision,
                round(probability, 4),
                round(confidence, 4),
                round(score, 4),
                reasons,
                xg_source,
            )
        )
    return decisions
