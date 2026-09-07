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
    "brain_v3_selection_field_not_complete",
    "brain_v3_selection_score_too_low",
    "brain_v3_selection_stronger_match",
    "brain_v3_selection_time_insufficient",
    "brain_v3_selection_live_probability_thin",
    "brain_v3_selection_candidate_deteriorating",
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


def _self_check(decision: dict[str, Any]) -> dict[str, Any]:
    """Ask whether LIVE itself leaves enough time and goal mass for this entry.

    This intentionally ignores PREMATCH/trend boosts when deciding whether the
    clock is still good enough.  A historical trend may support a real LIVE
    scenario, but it must never rescue a match whose current goal rate is too thin.
    """
    period = str(decision.get("period") or "")
    minute = int(_number(decision.get("minute"), 0.0))
    minutes_left = max(0.0, _number(decision.get("minutes_left"), 0.0))
    live_probability = max(0.0, min(1.0, _number(decision.get("live_probability"), 0.0)))
    expected = max(0.0, _number(decision.get("expected_goals_remaining"), 0.0))
    quality = max(0.0, min(1.0, _number(decision.get("data_quality"), 0.0)))
    counter = dict(decision.get("countercase") or {})
    under_risk = max(0.0, min(1.0, _number(counter.get("no_more_goal_risk"), 0.0)))

    if period == "1H":
        live_floor = _env_float("GOOL_BRAIN_V3_SELECTION_LIVE_MIN_1H", 0.64)
        expected_floor = _env_float("GOOL_BRAIN_V3_SELECTION_XG_REMAIN_MIN_1H", 1.00)
        time_floor = _env_float("GOOL_BRAIN_V3_SELECTION_TIME_LEFT_MIN_1H", 10.0)
        if minute >= 32:
            live_floor = max(live_floor, 0.66)
            expected_floor = max(expected_floor, 1.05)
    else:
        live_floor = _env_float("GOOL_BRAIN_V3_SELECTION_LIVE_MIN_2H", 0.65)
        expected_floor = _env_float("GOOL_BRAIN_V3_SELECTION_XG_REMAIN_MIN_2H", 1.05)
        time_floor = _env_float("GOOL_BRAIN_V3_SELECTION_TIME_LEFT_MIN_2H", 17.0)
        if minute >= 70:
            live_floor = max(live_floor, 0.67)
            expected_floor = max(expected_floor, 1.10)

    live_floor = max(0.55, min(0.85, live_floor))
    expected_floor = max(0.70, min(1.60, expected_floor))
    time_floor = max(5.0, min(25.0, time_floor))

    reasons: list[str] = []
    doubts: list[str] = []
    if minutes_left < time_floor:
        reasons.append("clock_too_short")
    if expected < expected_floor:
        reasons.append("expected_goal_mass_too_low")
    if live_probability < live_floor:
        reasons.append("pure_live_probability_too_low")
    if under_risk >= 0.55:
        doubts.append("meaningful_no_more_goal_countercase")
    if quality < 0.60:
        doubts.append("data_quality_not_strong")

    return {
        "version": 1,
        "passed": not reasons,
        "minutes_left": round(minutes_left, 2),
        "time_floor": round(time_floor, 2),
        "live_probability": round(live_probability, 4),
        "live_floor": round(live_floor, 4),
        "expected_goals_remaining": round(expected, 4),
        "expected_floor": round(expected_floor, 4),
        "under_risk": round(under_risk, 4),
        "reasons": reasons,
        "doubts": doubts,
        "question": "do_live_rate_and_clock_still_justify_one_more_goal",
    }


def _eligible(decision: dict[str, Any], self_check: dict[str, Any]) -> bool:
    counter = dict(decision.get("countercase") or {})
    probability = _number(decision.get("probability"), 0.0)
    bet_min = _number(decision.get("bet_min"), 1.0)
    return bool(
        bool(decision.get("active"))
        and probability >= bet_min
        and bool(decision.get("live_foundation"))
        and bool(decision.get("sustained_pressure"))
        and not bool(counter.get("strong"))
        and bool(self_check.get("passed"))
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
    self_check = _self_check(decision)
    now = time.monotonic()

    # Two *completed football field scans* are required. Market rechecks do not
    # increment this counter because they are the same football snapshot with a
    # fresher price. This prevents fake "two confirmations" of one old state.
    scan_id = max(0, int(_number(record.get("runtime_field_scan_id"), 0.0)))
    confirmed_round = max(0, int(_number(record.get("runtime_field_scan_confirmed_round"), 0.0)))
    market_recheck = bool(record.get("runtime_market_recheck"))

    ttl = max(90.0, _env_float("GOOL_BRAIN_V3_SELECTION_POOL_TTL_SECONDS", 180.0))
    min_field_age = max(0.0, min(180.0, _env_float("GOOL_BRAIN_V3_SELECTION_MATURITY_SECONDS", 35.0)))
    min_scans = max(2, min(4, _env_int("GOOL_BRAIN_V3_SELECTION_MIN_FIELD_SCANS", 2)))
    rival_gap = max(0.005, min(0.08, _env_float("GOOL_BRAIN_V3_SELECTION_RIVAL_GAP", 0.015)))
    max_rival_age = max(45.0, min(ttl, _env_float("GOOL_BRAIN_V3_SELECTION_RIVAL_MAX_AGE_SECONDS", 120.0)))
    max_drop = max(0.015, min(0.12, _env_float("GOOL_BRAIN_V3_SELECTION_MAX_SCORE_DROP", 0.045)))
    max_prob_drop = max(0.015, min(0.12, _env_float("GOOL_BRAIN_V3_SELECTION_MAX_PROB_DROP", 0.045)))
    floor = _selection_floor(decision)

    with _LOCK:
        _prune(now, ttl)
        previous = dict(_POOL.get(match_id) or {})
        first_seen = float(previous.get("first_seen") or now)
        observations = int(previous.get("observations") or 0) + 1
        fresh_scans = int(previous.get("fresh_scans") or 0)
        last_scan_id = int(previous.get("last_scan_id") or 0)
        previous_fresh_score = _number(previous.get("last_fresh_score"), score)
        previous_fresh_probability = _number(
            previous.get("last_fresh_probability"),
            _number(decision.get("probability"), 0.0),
        )

        is_new_fresh_scan = bool(not market_recheck and scan_id > 0 and scan_id != last_scan_id)
        if is_new_fresh_scan:
            fresh_scans += 1
            last_scan_id = scan_id
            last_fresh_score = score
            last_fresh_probability = _number(decision.get("probability"), 0.0)
            last_fresh_seen = now
        else:
            last_fresh_score = _number(previous.get("last_fresh_score"), score)
            last_fresh_probability = _number(
                previous.get("last_fresh_probability"),
                _number(decision.get("probability"), 0.0),
            )
            last_fresh_seen = float(previous.get("last_fresh_seen") or now)

        deteriorating = bool(
            is_new_fresh_scan
            and int(previous.get("fresh_scans") or 0) >= 1
            and (
                score < previous_fresh_score - max_drop
                or _number(decision.get("probability"), 0.0) < previous_fresh_probability - max_prob_drop
            )
        )
        eligible = _eligible(decision, self_check) and not deteriorating

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
            "fresh_scans": fresh_scans,
            "last_scan_id": last_scan_id,
            "last_fresh_score": round(last_fresh_score, 4),
            "last_fresh_probability": round(last_fresh_probability, 4),
            "last_fresh_seen": last_fresh_seen,
            "deteriorating": deteriorating,
            "self_check": self_check,
        }
        rivals = [
            dict(row)
            for mid, row in _POOL.items()
            if mid != match_id
            and bool(row.get("eligible"))
            and str(row.get("strategy") or "") == strategy
            and now - float(row.get("last_fresh_seen") or 0.0) <= max_rival_age
        ]

    age = max(0.0, now - first_seen)
    field_complete = bool(last_scan_id > 0 and confirmed_round >= last_scan_id)
    stronger = None
    if rivals:
        top = max(rivals, key=lambda row: float(row.get("score") or 0.0))
        if float(top.get("score") or 0.0) >= score + rival_gap:
            stronger = top

    status_before = str(decision.get("status") or "WATCH")
    blocks = [str(row) for row in list(decision.get("blocks") or []) if str(row) not in _DYNAMIC_BLOCKS]
    verdict = "KEEP"

    if status_before == "BET":
        if not bool(self_check.get("passed")):
            decision["status"] = "READY"
            reasons = set(self_check.get("reasons") or [])
            if "clock_too_short" in reasons or "expected_goal_mass_too_low" in reasons:
                blocks.append("brain_v3_selection_time_insufficient")
                verdict = "BLOCK_TIME"
            else:
                blocks.append("brain_v3_selection_live_probability_thin")
                verdict = "BLOCK_LIVE_THIN"
        elif fresh_scans < min_scans or age < min_field_age:
            decision["status"] = "READY"
            blocks.append("brain_v3_selection_gathering_field")
            verdict = "WAIT_FIELD_SCANS"
        elif not field_complete:
            decision["status"] = "READY"
            blocks.append("brain_v3_selection_field_not_complete")
            verdict = "WAIT_FIELD_COMPLETE"
        elif deteriorating:
            decision["status"] = "READY"
            blocks.append("brain_v3_selection_candidate_deteriorating")
            verdict = "BLOCK_DETERIORATING"
        elif score < floor:
            decision["status"] = "READY"
            blocks.append("brain_v3_selection_score_too_low")
            verdict = "BLOCK_LOW_SELECTION"
        elif stronger is not None:
            decision["status"] = "READY"
            blocks.append("brain_v3_selection_stronger_match")
            verdict = "BLOCK_STRONGER_MATCH"

    tournament = {
        "version": 3,
        "score": round(score, 4),
        "floor": round(floor, 4),
        "observations": observations,
        "fresh_field_scans": fresh_scans,
        "required_field_scans": min_scans,
        "last_scan_id": last_scan_id,
        "confirmed_round": confirmed_round,
        "field_complete": field_complete,
        "age_seconds": round(age, 2),
        "maturity_seconds": round(min_field_age, 2),
        "rival_gap": round(rival_gap, 4),
        "eligible": eligible,
        "rivals": len(rivals),
        "stronger_match": stronger,
        "deteriorating": deteriorating,
        "self_check": self_check,
        "verdict": verdict,
    }
    decision["selection_tournament"] = tournament
    decision["self_check"] = self_check
    decision["blocks"] = list(dict.fromkeys(blocks))
    selection["tournament"] = tournament
    selection["why_this_match"] = (
        "two_full_scans_field_champion" if verdict == "KEEP" and str(decision.get("status") or "") == "BET"
        else verdict.lower()
    )
    decision["selection_audit"] = selection
    record["brain_v3_decision"] = decision
    _sync_expert(experts, decision)

    print(
        f"GOOL_BRAIN_V3_SELECT match={match_id} strategy={strategy} "
        f"stage={status_before}->{decision.get('status')} score={score:.3f} floor={floor:.3f} "
        f"scans={fresh_scans}/{min_scans} round={last_scan_id}/{confirmed_round} "
        f"age={age:.1f}s rivals={len(rivals)} better={(stronger or {}).get('match_id') or '-'} "
        f"time={float(self_check.get('minutes_left') or 0):.0f}m "
        f"live={float(self_check.get('live_probability') or 0):.3f} "
        f"xgrem={float(self_check.get('expected_goals_remaining') or 0):.2f} "
        f"verdict={verdict}",
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
        "GOOL_BRAIN_V3_SELECTION installed mode=two_full_field_scans+self_check+tournament "
        f"1h_floor={_env_float('GOOL_BRAIN_V3_SELECTION_MIN_1H', 0.72):.2f} "
        f"2h_floor={_env_float('GOOL_BRAIN_V3_SELECTION_MIN_2H', 0.69):.2f} "
        f"field_scans={_env_int('GOOL_BRAIN_V3_SELECTION_MIN_FIELD_SCANS', 2)}",
        flush=True,
    )


def _reset_pool_for_tests() -> None:
    with _LOCK:
        _POOL.clear()


__all__ = [
    "install_brain_v3_selection_hardening",
    "_apply_tournament",
    "_self_check",
    "_reset_pool_for_tests",
]
