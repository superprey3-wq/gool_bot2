from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Callable

from .multi_router import RouterDecision


_INSTALLED = False


def _number(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return float(default)


def _full_match_strategy(match: dict[str, Any]) -> str | None:
    if bool(match.get("is_finished")) or bool(match.get("is_halftime")):
        return None
    try:
        minute = int(match.get("minute") or 0)
    except (TypeError, ValueError):
        return None
    if 1 <= minute <= 45:
        return "goal_before_ht"
    if 46 <= minute <= 95:
        return "another_goal"
    return None


def _full_match_routing_experts(
    match: dict[str, Any],
    experts: dict[str, Any],
) -> dict[str, Any]:
    strategy = _full_match_strategy(match)
    if strategy is None:
        return {}
    expert = experts.get(strategy)
    return {strategy: expert} if isinstance(expert, dict) else {}


def _full_match_entry_cutoff(decision: RouterDecision) -> RouterDecision:
    """No fixed 35'/75' veto: ordinary GOOL remains eligible through 95'."""
    if decision.status != "BET" or decision.winner is None:
        return decision
    winner = decision.winner
    if str(getattr(winner, "source", "")).startswith("1xbet:autonomous_steam"):
        return decision
    try:
        minute = int(decision.minute)
    except (TypeError, ValueError):
        minute = 0
    if 1 <= minute <= 95:
        return decision

    if "concept_entry_outside_full_match" not in winner.blocks:
        winner.blocks.append("concept_entry_outside_full_match")
    winner.eligible = False
    if not any(row.key == winner.key for row in decision.rejected):
        decision.rejected.append(winner)
    decision.status = "WAIT"
    decision.winner = None
    decision.alternatives = []
    decision.reason = "WAIT: обычный GOOL принимает новые входы только в игровых минутах 1-95."
    return decision


def _brain_v3_dynamic_analyzer(
    original: Callable[..., RouterDecision],
) -> Callable[..., RouterDecision]:
    def analyze(
        match: dict[str, Any],
        market_row: dict[str, Any] | None,
        experts: dict[str, Any],
        *,
        data_quality: float = 1.0,
    ) -> RouterDecision:
        result = original(match, market_row, experts, data_quality=data_quality)
        if result.status != "WAIT":
            return result

        strategy = _full_match_strategy(match)
        expert = experts.get(strategy) if strategy else None
        if not isinstance(expert, dict):
            return result
        if not str(expert.get("source") or "").startswith("brain_v3:"):
            return result
        if str(expert.get("state") or "").upper() != "PASS" or not bool(expert.get("passed")):
            return result

        diagnostics = dict(expert.get("diagnostics") or {})
        brain = dict(diagnostics.get("brain_v3") or {})
        if str(brain.get("status") or "") != "BET":
            return result

        candidate = next(
            (
                row
                for row in list(result.rejected or [])
                if str(getattr(row, "source", "")).startswith("brain_primary:")
                and str(getattr(row, "strategy", "")) == strategy
            ),
            None,
        )
        if candidate is None:
            return result

        # Brain V3 has already applied its own dynamic BET floor. STANDARD stays
        # at its stricter ~70%+ floor; guarded STRONG_LIVE may legitimately pass
        # below 70. Remove only the obsolete second fixed-rating veto here.
        remaining = [
            str(block)
            for block in list(candidate.blocks or [])
            if not str(block).startswith("brain_rating_below_")
        ]
        if remaining:
            candidate.blocks = remaining
            return result

        candidate.blocks = []
        candidate.eligible = True
        tags = list(candidate.reason_tags or [])
        if "brain_v3_dynamic_floor" not in tags:
            tags.append("brain_v3_dynamic_floor")
        entry_mode = str(brain.get("entry_mode") or "STANDARD")
        if entry_mode == "STRONG_LIVE" and "strong_live" not in tags:
            tags.append("strong_live")
        candidate.reason_tags = tags
        threshold = 100.0 * _number(brain.get("bet_min"), 0.70)
        rating = _number(getattr(candidate, "rating", 0.0), 0.0)
        return RouterDecision(
            "BET",
            result.minute,
            result.score,
            candidate,
            [],
            [row for row in list(result.rejected or []) if row is not candidate],
            f"GOOL LIVE Brain V3 дал PASS {rating:.0f}/100; динамический порог {threshold:.0f}/100.",
        )

    return analyze


def _production_brain_audit(
    record: dict[str, Any],
    experts: dict[str, Any],
    decision: dict[str, Any],
) -> dict[str, Any]:
    """Keep safety countercase/selection quality, but stop matches competing globally.

    A valid match should not become WAIT merely because another valid match is a
    little stronger.  Also, two fresh football scans are enough by themselves;
    an unrelated 1xBet market refresh must never be required to complete GOOL.
    """
    if not isinstance(decision, dict) or not bool(decision.get("active")):
        return decision

    from . import brain_v3_decision_audit as audit
    from . import brain_v3_selection_hardening as selection

    match = dict(record.get("match") or {})
    match_id = str(match.get("flashscore_event_id") or "").strip()
    if not match_id:
        return decision

    pre_status = str(decision.get("status") or "WATCH")
    countercase = audit._under_countercase(decision)
    decision["countercase"] = countercase
    score, score_reasons = audit._selection_score(decision, countercase)

    blocks = [
        str(row)
        for row in list(decision.get("blocks") or [])
        if str(row) not in getattr(audit, "_DYNAMIC_BLOCKS", set())
    ]
    verdict = "KEEP"
    if pre_status == "BET" and bool(countercase.get("strong")):
        decision["status"] = "READY"
        blocks.append("brain_v3_under_countercase_strong")
        verdict = "BLOCK_UNDER"

    probability = _number(decision.get("probability"), 0.0)
    bet_min = _number(decision.get("bet_min"), 0.70)
    if str(decision.get("status") or "") != "BET":
        if probability < bet_min:
            blocks.append("brain_v3_probability_below_bet")
        elif not bool(countercase.get("strong")):
            blocks.append("brain_v3_wait_confirmation")

    decision["selection_audit"] = {
        "version": 2,
        "score": round(score, 4),
        "score_reasons": score_reasons,
        "strategy": str(decision.get("strategy") or ""),
        "better_candidate": None,
        "verdict": verdict,
        "why_this_match": "independent_live_quality",
        "global_rival_veto": False,
    }
    decision["blocks"] = list(dict.fromkeys(blocks))
    thoughts = list(decision.get("thoughts") or [])
    thoughts.append(f"under_risk={float(countercase.get('no_more_goal_risk') or 0):.2f}")
    thoughts.append(f"selection={score:.2f}/{verdict}")
    decision["thoughts"] = thoughts
    audit._sync_expert(experts, decision)

    # During fresh field scan N, the worker marks scan N-1 as globally completed.
    # For this match we already hold its fresh scan-N snapshot, so that is enough
    # to establish two independent football observations.  Do not wait for a
    # market-only recheck just to turn confirmed_round from N-1 into N.
    selection_record = dict(record)
    scan_id = max(0, int(_number(record.get("runtime_field_scan_id"), 0.0)))
    confirmed = max(0, int(_number(record.get("runtime_field_scan_confirmed_round"), 0.0)))
    market_recheck = bool(record.get("runtime_market_recheck"))
    if not market_recheck and scan_id > 0 and confirmed >= max(0, scan_id - 1):
        selection_record["runtime_field_scan_confirmed_round"] = scan_id

    out = selection._apply_tournament(selection_record, experts, decision)
    tournament = dict(out.get("selection_tournament") or {})

    # Selection still calculates the stronger rival for diagnostics, but an
    # otherwise-valid BET is not discarded only because another match is better.
    if tournament.get("verdict") == "BLOCK_STRONGER_MATCH":
        out["status"] = "BET"
        out["blocks"] = [
            str(block)
            for block in list(out.get("blocks") or [])
            if str(block) != "brain_v3_selection_stronger_match"
        ]
        tournament["verdict"] = "KEEP_INDEPENDENT_MATCH"
        tournament["global_rival_veto"] = False
        out["selection_tournament"] = tournament
        selection_audit = dict(out.get("selection_audit") or {})
        selection_audit["tournament"] = tournament
        selection_audit["why_this_match"] = "independent_live_quality_passed"
        out["selection_audit"] = selection_audit
        selection._sync_expert(experts, out)

    record["brain_v3_decision"] = out
    print(
        f"GOOL_BRAIN_V3_THROUGHPUT match={match_id} stage={pre_status}->{out.get('status')} "
        f"scan={scan_id}/{confirmed} independent_rival=1 under={float(countercase.get('no_more_goal_risk') or 0):.2f}",
        flush=True,
    )
    return out


def _target_aware_brain_entry(
    record: dict[str, Any],
    decision: RouterDecision,
) -> dict[str, Any] | None:
    """Deduplicate the same target line, not the whole match forever."""
    from . import brain_primary_mode as primary

    winner = decision.winner
    if winner is None or not str(winner.source or "").startswith("brain_primary:"):
        return None
    match = record.get("match") or {}
    match_id = str(match.get("flashscore_event_id") or "")
    if not match_id:
        return None
    strategy = str(winner.strategy or "")
    signal_key = f"{match_id}:{strategy}:{winner.key}"

    with primary._LOCK:
        state = primary._load_signal_state(primary._signal_state_path())
        signals = state.get("signals") or {}
        if signal_key in signals:
            return None
        # Respect an old-format signal only when it was for this exact target.
        legacy = signals.get(f"{match_id}:{strategy}")
        if isinstance(legacy, dict) and str(legacy.get("market") or "") == str(winner.label or ""):
            return None

    return {
        "signal_key": signal_key,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "mode": "signal_only",
        "match_id": match_id,
        "home": match.get("home"),
        "away": match.get("away"),
        "league": match.get("league"),
        "minute": int(match.get("minute") or 0),
        "score": [int(match.get("home_score") or 0), int(match.get("away_score") or 0)],
        "strategy": strategy,
        "head": strategy,
        "market": winner.label,
        "odd": float(winner.odd or 0.0),
        "price_available": bool(float(winner.odd or 0.0) > 1.0),
        "probability": float(winner.model_probability or 0.0),
        "event_score": float(winner.rating or 0.0),
        "confidence_score": float(winner.rating or 0.0),
        "source": winner.source,
        "signal_source": "GOOL_BRAIN",
        "result": "signal_only",
    }


def install_production_signal_throughput() -> None:
    global _INSTALLED
    if _INSTALLED:
        return

    from . import brain_primary_mode as primary
    from . import brain_v3_activation as activation
    from . import multi_runtime as runtime

    # install_multi_product() calls brain_primary_mode.install_runtime_patches()
    # before this installer, so runtime.analyze_multi_match is the final public
    # Brain-primary analyzer at this point.
    original_analyze = runtime.analyze_multi_match

    primary._active_strategy = _full_match_strategy
    primary._brain_entry = _target_aware_brain_entry
    runtime.routing_experts = _full_match_routing_experts
    runtime.analyze_multi_match = _brain_v3_dynamic_analyzer(original_analyze)
    runtime.enforce_entry_cutoff = _full_match_entry_cutoff
    activation.audit_brain_v3_decision = _production_brain_audit

    _INSTALLED = True
    print(
        "GOOL_SIGNAL_THROUGHPUT installed full_match=1-45+46-95 "
        "strong_live_dynamic_floor=on xbet_recheck_required=off global_rival_veto=off "
        "dedupe=target_line",
        flush=True,
    )


__all__ = [
    "install_production_signal_throughput",
    "_full_match_strategy",
    "_full_match_routing_experts",
    "_production_brain_audit",
]
