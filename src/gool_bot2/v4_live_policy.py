from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class LiveV4Input:
    market: str
    minute: int
    probability: float
    data_quality: float
    pressure: float = 0.0
    trend: float = 0.0
    expected_remaining: float = 0.0
    score_synced: bool = True
    market_suspended: bool = False
    post_goal_guard: bool = False


@dataclass(frozen=True)
class LiveV4Decision:
    allowed: bool
    tier: str | None
    score: float
    reason: str


def _window_ok(market: str, minute: int) -> bool:
    if market == "goal_before_ht":
        return 8 <= minute <= 43
    if market == "another_goal":
        return 46 <= minute <= 88
    return False


def decide_live_v4(row: LiveV4Input) -> LiveV4Decision:
    """Throughput-friendly gate for the two public V4 LIVE systems.

    Only objective bad-state conditions are hard vetoes. Football evidence
    contributes to rank/tier instead of becoming a stack of independent vetoes.
    """
    if row.market not in {"goal_before_ht", "another_goal"}:
        return LiveV4Decision(False, None, 0.0, "unsupported_market")
    if not _window_ok(row.market, row.minute):
        return LiveV4Decision(False, None, 0.0, "outside_window")
    if not row.score_synced:
        return LiveV4Decision(False, None, 0.0, "score_not_synced")
    if row.market_suspended:
        return LiveV4Decision(False, None, 0.0, "market_suspended")
    if row.post_goal_guard:
        return LiveV4Decision(False, None, 0.0, "post_goal_guard")
    if row.data_quality < 0.50:
        return LiveV4Decision(False, None, 0.0, "poor_data")

    p = max(0.0, min(1.0, row.probability))
    quality = max(0.0, min(1.0, row.data_quality))
    pressure = max(0.0, min(1.0, row.pressure))
    trend = max(0.0, min(1.0, row.trend))
    remaining = max(0.0, min(1.0, row.expected_remaining / 1.25))

    score = 100.0 * (
        0.58 * p
        + 0.14 * quality
        + 0.11 * pressure
        + 0.07 * trend
        + 0.10 * remaining
    )

    # Late 2H remains possible, but needs more evidence naturally through rank.
    normal_floor = 60.0 if row.market == "goal_before_ht" else 61.0
    strong_floor = 72.0 if row.market == "goal_before_ht" else 73.0
    if row.market == "another_goal" and row.minute >= 80:
        normal_floor += 2.0
        strong_floor += 2.0

    if score >= strong_floor:
        return LiveV4Decision(True, "STRONG", round(score, 2), "strong_rank")
    if score >= normal_floor:
        return LiveV4Decision(True, "NORMAL", round(score, 2), "normal_rank")
    return LiveV4Decision(False, None, round(score, 2), "rank_too_low")
