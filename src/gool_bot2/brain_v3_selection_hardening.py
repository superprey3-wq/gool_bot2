from __future__ import annotations

import os
import threading
import time
from typing import Any, Callable


_INSTALLED = False
_LOCK = threading.RLock()
_POOL: dict[str, dict[str, Any]] = {}
_DYNAMIC_BLOCKS = {
    "brain_v3_selection_gathering_field",
    "brain_v3_selection_score_too_low",
    "brain_v3_selection_stronger_match",
}


def _number(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return float(default)


def _env_float(name: str, default: float) -> float:
    return _number(os.getenv(name), default)


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        return int(default)


def _sync_expert(experts: dict[str, Any], decision: dict[str, Any]) -> None:
    strategy = str(decision.get("strategy") or "")
    expert = experts.get(strategy) if strategy else None
    if not isinstance(expert, dict) or not str(expert.get("source") or "").startswith("brain_v3:"):
        return
    status = str(decision.get("status") or "WATCH")
    expert["passed"] = status == "BET"
    expert["state"] = "PASS" if status == "BET" else "BORDERLINE"
    expert["blocks"] = list(decision.get("blocks") or [])
    diagnostics = dict(expert.get("diagnostics") or {})
    diagnostics["brain_v3"] = decision
    expert["diagnostics"] = diagnostics


def _selection_floor(decision: dict[str, Any]) -> float:
    period = str(decision.get("period") or "")
    if period == "1H":
        return max(0.60, min(0.90, _env_float("GOOL_BRAIN_V3_SELECTION_MIN_1H", 0.72)))
    return max(0.60, min(0.90, _env_float("GOOL_BRAIN_V3_SELECTION_MIN_2H", 0.69)))


def _eligible(decision: dict[str, Any]) -> bool:
    counter = dict(decision.get("countercase") or {})
    probability = _number(decision.get("probability"), 0.0)
    bet_min = _number(decision.get("bet_min"), 1.0)
    return bool(
        bool(decision.get("active"))
        and probability >= bet_min
        and bool(decision.get("live_foundation"))
        and bool(decision.get("sustained_pressure"))
        and not bool(counter.get("strong"))
        and str(decision.get("strategy") or "") in {"goal_before_ht", "another_goal"}
    )


def _prune(now: float, ttl: float) -> None:
    stale = [mid for mid, row in _POOL.items() if now - float(row.get("last_seen") or 0.0) > ttl]
    for mid in stale:
        _POOL.pop(mid, None)


def _apply_tournament(
    record: dict[str, Any],
    experts: dict[str, Any],
    decision: dict[str, Any],
) -> dict[str, Any]:
    if not isinstance(decision, dict) or not bool(decision.get("active")):
        return decision

    match = dict(record.get("match") or {})
    match_id = str(match.get("flashscore_event_id") or "").strip()
    strategy = str(decision.get("strategy") or "")
    if not match_id or strategy not in {"goal_before_ht", "another_goal"}:
        return decision

    selection = dict(decision.get("selection_audit") or {})
    score = _number(selection.get("score"), 0.0)
    now = time.monotonic()
    ttl = max(25.0, _env_float("GOOL_BRAIN_V3_SELECTION_POOL_TTL_SECONDS", 45.0))
    min_age = max(0.0, min(30.0, _env_float("GOOL_BRAIN_V3_SELECTION_MATURITY_SECONDS", 8.0)))
    min_observations = max(2, min(5, _env_int("GOOL_BRAIN_V3_SELECTION_MIN_OBSERVATIONS", 2)))
    rival_gap = max(0.005, min(0.08, _env_float("GOOL_BRAIN_V3_SELECTION_RIVAL_GAP", 0.015)))
    floor = _selection_floor(decision)
    eligible = _eligible(decision)

    with _LOCK:
        _prune(now, ttl)
        previous = dict(_POOL.get(match_id) or {})
        first_seen = float(previous.get("first_seen") or now)
        observations = int(previous.get("observations") or 0) + 1
        _POOL[match_id] = {
            "match_id": match_id,
            "home": match.get("home"),
            "away": match.get("away"),
            "minute": int(_number(decision.get("minute"), 0.0)),
            "period": str(decision.get("period") or ""),
            "strategy": strategy,
            "score": round(score, 4),
            "probability": round(_number(decision.get("probability"), 0.0), 4),
            "eligible": eligible,
            "first_seen": first_seen,
            "last_seen": now,
            "observations": observations,
        }
        rivals = [
            dict(row)
            for mid, row in _POOL.items()
            if mid != match_id
            and bool(row.get("eligible"))
            and str(row.get("strategy") or "") == strategy
        ]

    age = max(0.0, now - first_seen)
    stronger = None
    if rivals:
        top = max(rivals, key=lambda row: float(row.get("score") or 0.0))
        if float(top.get("score") or 0.0) >= score + rival_gap:
            stronger = top

    status_before = str(decision.get("status") or "WATCH")
    blocks = [str(row) for row in list(decision.get("blocks") or []) if str(row) not in _DYNAMIC_BLOCKS]
    verdict = "KEEP"

    if status_before == "BET":
        if observations < min_observations or age < min_age:
            decision["status"] = "READY"
            blocks.append("brain_v3_selection_gathering_field")
            verdict = "WAIT_FIELD"
        elif score < floor:
            decision["status"] = "READY"
            blocks.append("brain_v3_selection_score_too_low")
            verdict = "BLOCK_LOW_SELECTION"
        elif stronger is not None:
            decision["status"] = "READY"
            blocks.append("brain_v3_selection_stronger_match")
            verdict = "BLOCK_STRONGER_MATCH"

    tournament = {
        "version": 2,
        "score": round(score, 4),
        "floor": round(floor, 4),
        "observations": observations,
        "age_seconds": round(age, 2),
        "min_observations": min_observations,
        "maturity_seconds": round(min_age, 2),
        "rival_gap": round(rival_gap, 4),
        "eligible": eligible,
        "rivals": len(rivals),
        "stronger_match": stronger,
        "verdict": verdict,
    }
    decision["selection_tournament"] = tournament
    decision["blocks"] = list(dict.fromkeys(blocks))
    selection["tournament"] = tournament
    selection["why_this_match"] = (
        "mature_field_champion" if verdict == "KEEP" and str(decision.get("status") or "") == "BET"
        else verdict.lower()
    )
    decision["selection_audit"] = selection
    record["brain_v3_decision"] = decision
    _sync_expert(experts, decision)

    print(
        f"GOOL_BRAIN_V3_SELECT match={match_id} strategy={strategy} "
        f"stage={status_before}->{decision.get('status')} score={score:.3f} floor={floor:.3f} "
        f"obs={observations} age={age:.1f}s rivals={len(rivals)} "
        f"better={(stronger or {}).get('match_id') or '-'} verdict={verdict}",
        flush=True,
    )
    return decision


def install_brain_v3_selection_hardening() -> None:
    global _INSTALLED
    if _INSTALLED:
        return

    from . import brain_v3_activation as activation

    original: Callable[..., dict[str, Any]] = activation.audit_brain_v3_decision

    def hardened(
        record: dict[str, Any],
        experts: dict[str, Any],
        decision: dict[str, Any],
    ) -> dict[str, Any]:
        out = original(record, experts, decision)
        return _apply_tournament(record, experts, out)

    activation.audit_brain_v3_decision = hardened
    _INSTALLED = True
    print(
        "GOOL_BRAIN_V3_SELECTION installed mode=mature+tournament "
        f"1h_floor={_env_float('GOOL_BRAIN_V3_SELECTION_MIN_1H', 0.72):.2f} "
        f"2h_floor={_env_float('GOOL_BRAIN_V3_SELECTION_MIN_2H', 0.69):.2f} "
        f"maturity={_env_float('GOOL_BRAIN_V3_SELECTION_MATURITY_SECONDS', 8.0):.0f}s",
        flush=True,
    )


def _reset_pool_for_tests() -> None:
    with _LOCK:
        _POOL.clear()


__all__ = [
    "install_brain_v3_selection_hardening",
    "_apply_tournament",
    "_reset_pool_for_tests",
]
