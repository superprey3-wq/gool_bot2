from __future__ import annotations

import threading
from typing import Any

from .brain_v3_strong_live import apply_strong_live_entry as _apply_strong_live_entry


_INSTALLED = False
_DECISION_LOCK = threading.RLock()


def full_match_strategy(minute: int, halftime: bool) -> tuple[str | None, str | None, int]:
    """Keep ordinary GOOL Brain active for all playable minutes of both halves."""
    if halftime:
        return None, None, 0
    if 1 <= int(minute) <= 45:
        return "goal_before_ht", "1H", 47
    if 46 <= int(minute) <= 95:
        return "another_goal", "2H", 95
    return None, None, 0


def apply_full_match_brain_v3_to_experts(
    record: dict[str, Any],
    experts: dict[str, Any],
    *,
    data_quality: float,
    prematch_profile: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Evaluate Brain V3 with full-half windows without changing test/legacy callers.

    Brain V3's evaluator looks up its strategy-window helper at call time.  The
    production wrapper swaps that helper only for the duration of this evaluation
    under a lock, then restores the original helper.  This keeps rollback and
    diagnostic callers on their historical semantics while ordinary production
    GOOL sees the full 1H/2H clock.
    """
    from . import brain_v3_decision as decision_module

    with _DECISION_LOCK:
        original = decision_module._active_strategy
        decision_module._active_strategy = full_match_strategy
        try:
            out = decision_module.apply_brain_v3_to_experts(
                record,
                experts,
                data_quality=data_quality,
                prematch_profile=prematch_profile,
            )
        finally:
            decision_module._active_strategy = original

    if isinstance(out, dict) and bool(out.get("active")):
        out["full_match_clock"] = True
        record["brain_v3_decision"] = out
    return out


def _install_selection_clock_patch() -> None:
    """Remove only the duplicate fixed clock veto for full-match production rows.

    The football probability already shrinks with ``minutes_left`` because Brain V3
    calculates expected remaining goals over the remaining horizon.  Keep expected
    goal mass, pure-LIVE probability, UNDER countercase, quality, field-scan
    maturity and rival-selection checks intact.
    """
    from . import brain_v3_selection_hardening as selection

    original = selection._self_check
    if bool(getattr(original, "_gool_full_match_clock", False)):
        return

    def full_match_self_check(decision: dict[str, Any]) -> dict[str, Any]:
        result = dict(original(decision))
        if not bool(decision.get("full_match_clock")):
            return result
        reasons = [str(reason) for reason in list(result.get("reasons") or [])]
        reasons = [reason for reason in reasons if reason != "clock_too_short"]
        result["time_floor"] = 0.0
        result["reasons"] = reasons
        result["passed"] = not reasons
        result["full_match_clock"] = True
        return result

    full_match_self_check._gool_full_match_clock = True  # type: ignore[attr-defined]
    selection._self_check = full_match_self_check


def install_brain_v3_full_match() -> None:
    """Enable full-match production checks and remove duplicate fixed clock veto."""
    global _INSTALLED
    if _INSTALLED:
        return

    _install_selection_clock_patch()
    _INSTALLED = True
    print(
        "GOOL_BRAIN_V3_FULL_MATCH installed first_half=1-45 second_half=46-95 halftime=paused fixed_clock_veto=off",
        flush=True,
    )


def apply_full_match_strong_live(
    record: dict[str, Any],
    experts: dict[str, Any],
    decision: dict[str, Any],
) -> dict[str, Any]:
    """Apply guarded STRONG_LIVE without the old hard 70-minute cutoff.

    The existing STRONG_LIVE implementation already becomes stricter after 65'
    and still requires sustained pressure, LIVE foundation, data quality, recent
    attacking evidence and enough expected remaining goal mass.  For 70'+ we only
    neutralize its obsolete ``minute < 70`` gate; every football safeguard uses
    the real late-match data and remains in force.  Late draws stay excluded from
    the relaxed path and can only pass the stricter standard Brain route.
    """
    if not isinstance(decision, dict) or not bool(decision.get("active")):
        return decision

    actual_minute = int(decision.get("minute") or 0)
    if actual_minute < 70:
        return _apply_strong_live_entry(record, experts, decision)

    period = str(decision.get("period") or "")
    if period != "2H" or actual_minute > 95:
        return decision

    # The old helper has exactly one hard late-time gate (minute < 70). Feeding
    # 69 only for that gate keeps its >=65 late floors active. All probability,
    # expected-goal, pressure, score and quality fields remain the real values
    # calculated at the actual minute.
    candidate = dict(decision)
    candidate["minute"] = 69
    result = _apply_strong_live_entry(record, experts, candidate)
    result["minute"] = actual_minute
    strong = dict(result.get("strong_live") or {})
    if strong:
        strong["full_match_clock"] = True
        strong["actual_minute"] = actual_minute
        result["strong_live"] = strong
    result["full_match_clock"] = True
    record["brain_v3_decision"] = result
    return result


__all__ = [
    "apply_full_match_brain_v3_to_experts",
    "apply_full_match_strong_live",
    "full_match_strategy",
    "install_brain_v3_full_match",
]
