from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class PrefilterResult:
    score: float
    candidate: bool
    reasons: tuple[str, ...]


def football_prefilter(stats: dict[str, tuple[float, float]], minute: float, threshold: float = 50.0) -> PrefilterResult:
    """Cheap football-only candidate score used before expensive enrichment/AI.

    It is intentionally permissive. The score is not a calibrated probability;
    it only decides which matches deserve richer provider calls and model work.
    """
    def total(name: str) -> float:
        values = stats.get(name)
        return float(sum(values)) if values else 0.0

    xg = total("xg")
    xgot = total("xgot")
    shots = total("shots")
    sot = total("shots_on_target")
    big = total("big_chances")
    inside = total("shots_inside_box")
    touches = total("touches_box")
    corners = total("corners")

    score = 0.0
    reasons: list[str] = []

    if xg >= 0.55:
        score += 18
        reasons.append(f"xg={xg:.2f}")
    elif xg >= 0.30:
        score += 10

    if xgot >= 0.40:
        score += 16
        reasons.append(f"xgot={xgot:.2f}")
    elif xgot >= 0.20:
        score += 8

    if shots >= 8:
        score += 14
        reasons.append(f"shots={shots:.0f}")
    elif shots >= 5:
        score += 8

    if sot >= 3:
        score += 16
        reasons.append(f"sot={sot:.0f}")
    elif sot >= 2:
        score += 9

    if big >= 1:
        score += min(16.0, 8.0 * big)
        reasons.append(f"big={big:.0f}")
    if inside >= 4:
        score += 8
    if touches >= 14:
        score += 6
    if corners >= 5:
        score += 6

    # A little time context: the same raw pressure is more meaningful once a
    # match has accumulated enough live evidence, but this never gates by odds.
    if 15 <= minute <= 75:
        score += 4

    score = min(100.0, score)
    return PrefilterResult(score=score, candidate=score >= float(threshold), reasons=tuple(reasons))
