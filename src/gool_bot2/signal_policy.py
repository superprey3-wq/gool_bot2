from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class GateResult:
    allowed: bool
    reasons: tuple[str, ...] = ()


def _pending(open_signals: list[dict[str, Any]], match_id: str) -> list[dict[str, Any]]:
    return [
        row for row in open_signals
        if str(row.get("match_id") or row.get("event_id") or "") == str(match_id)
        and str(row.get("result") or "pending").lower() in {"", "pending", "wait", "waiting"}
    ]


def exposure_gate(match_id: str, all_signals: list[dict[str, Any]], max_entries: int = 2, max_open: int = 1) -> GateResult:
    rows = [row for row in all_signals if str(row.get("match_id") or row.get("event_id") or "") == str(match_id)]
    reasons: list[str] = []
    if len(rows) >= max_entries:
        reasons.append(f"max_entries={max_entries}")
    if len(_pending(all_signals, match_id)) >= max_open:
        reasons.append(f"max_open={max_open}")
    return GateResult(not reasons, tuple(reasons))


def time_gate(head: str, minute: int, is_halftime: bool = False, is_reentry: bool = False) -> GateResult:
    minute = int(minute or 0)
    reasons: list[str] = []
    if head == "another_goal":
        if minute < 10:
            reasons.append("warmup_until_10")
        if 46 <= minute < 55:
            reasons.append("second_half_warmup_until_55")
        limit = 80 if is_reentry else 75
        if minute > limit:
            reasons.append(f"entry_window_closed_{limit}")
    elif head == "goal_before_ht":
        if is_halftime or minute > 25:
            reasons.append("first_half_signal_window_closed_25")
        elif minute < 15:
            reasons.append("collecting_until_15")
    elif head == "two_plus_goals_second_half":
        if not is_halftime and not (47 <= minute <= 50):
            reasons.append("requires_halftime_or_47_50")
    else:
        reasons.append("unknown_head")
    return GateResult(not reasons, tuple(reasons))


def post_goal_gate(minute: int, last_goal_minute: int | None, cooldown_minutes: int = 5) -> GateResult:
    if last_goal_minute is None:
        return GateResult(True)
    since = int(minute or 0) - int(last_goal_minute)
    if since < cooldown_minutes:
        return GateResult(False, (f"post_goal_cooldown_{since}/{cooldown_minutes}",))
    return GateResult(True)


def model_threshold_gate(head: str, probability: float, score: float) -> GateResult:
    probability_pct = float(probability) * 100.0 if float(probability) <= 1.0 else float(probability)
    min_score = 75.0 if head == "another_goal" else 80.0
    min_probability = 0.0 if head == "another_goal" else 75.0
    reasons: list[str] = []
    if float(score) < min_score:
        reasons.append(f"score={float(score):.1f}<{min_score:.1f}")
    if probability_pct < min_probability:
        reasons.append(f"probability={probability_pct:.1f}<{min_probability:.1f}")
    return GateResult(not reasons, tuple(reasons))


def combine_gates(*gates: GateResult) -> GateResult:
    reasons = tuple(reason for gate in gates for reason in gate.reasons)
    return GateResult(all(gate.allowed for gate in gates), reasons)
