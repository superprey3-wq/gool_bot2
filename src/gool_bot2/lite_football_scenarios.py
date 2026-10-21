"""Separate football PREMATCH hypotheses and original LIVE V3 scenarios.

PREMATCH F01-F05 are registered hypotheses, not stand-alone bets.
LIVE V3 is driven by real Flashscore/secondary snapshots and original
Brain V3 memory, full-match/STRONG_LIVE and selection audit modules.
No bookmaker input affects probability.
"""
from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace
from typing import Any

from .match_context import provider_count, xg_or_proxy_pair


def prematch_scenarios(
    forecast: dict[str, Any],
    *,
    football_quote: dict[str, Any] | None = None,
    qualified_picks: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    from .prematch_strategy_scenarios import evaluate_prematch_scenarios
    # Original registry expects pick attributes (PrematchPick), not the
    # Lite transport dictionaries. Preserve the true V5 selection and price.
    verified = [
        SimpleNamespace(
            market=row.get("market"), selection=row.get("original_selection"),
            odds=row.get("odd"),
        ) for row in (qualified_picks or [])
        if isinstance(row, dict)
    ]
    return evaluate_prematch_scenarios(
        "football", football_forecast=forecast, football_quote=football_quote,
        qualified_picks=verified,
    )


def _original_data_quality(record: dict[str, Any]) -> float:
    """Exactly the source / xG / 5-10m weighting from multi_runtime._data_quality.

    Keep the copy local: importing the old multisport runtime starts heavy
    dependency chains on the 256 MiB football-only host.
    """
    providers = min(3, provider_count(record))
    provider_score = providers / 3.0
    try:
        _, _, xg_source, evidence = xg_or_proxy_pair(record)
    except Exception:
        xg_source, evidence = "unavailable", 0
    xg_score = 1.0 if xg_source == "provider_xg" else (
        0.65 if xg_source == "attack_proxy" and evidence >= 2 else 0.0
    )
    momentum = record.get("live_momentum") or {}
    keys = (
        "shots_total_last_5m", "sot_total_last_5m", "xg_total_last_5m",
        "shots_total_last_10m", "sot_total_last_10m", "xg_total_last_10m",
    )
    momentum_score = sum(momentum.get(k) is not None for k in keys) / len(keys)
    return max(0.0, min(1.0,
               0.48 * provider_score + 0.27 * xg_score + 0.25 * momentum_score))


def forget_live_history(match_id: str) -> None:
    """Explicitly discard stale epoch history after score / time discontinuity."""
    from .brain_v3_memory import _HISTORY
    _HISTORY.pop(str(match_id), None)


def evaluate_live_scenarios(
    record: dict[str, Any], match_id: str, *, observed_at: float,
) -> dict[str, Any]:
    """Run the original independent V3 strategy and pressure/scenario memory."""
    from .brain_v3_memory import build_brain_v3_memory, _HISTORY
    from .brain_v3_full_match import (
        install_brain_v3_full_match,
        apply_full_match_brain_v3_to_experts,
        apply_full_match_strong_live,
    )
    from .brain_v3_decision_audit import audit_brain_v3_decision
    from .brain_v3_selection_hardening import _apply_tournament
    from .threat_sequence_live import evaluate_live_threat

    record["captured_at"] = datetime.fromtimestamp(
        observed_at, timezone.utc
    ).isoformat()
    memory = build_brain_v3_memory(record, match_id)
    quality = _original_data_quality(record)
    experts: dict[str, dict[str, Any]] = {}
    # Use the production GOOL Brain V3 chain, rather than invoking only the
    # legacy 35'/73' basic evaluator. Each stage is the original module:
    # full-half clock -> strong live -> UNDER countercase/rival audit ->
    # two genuinely completed field scans and final selection.
    install_brain_v3_full_match()
    decision = apply_full_match_brain_v3_to_experts(
        record, experts, data_quality=quality,
    )
    decision = apply_full_match_strong_live(record, experts, decision)
    decision = audit_brain_v3_decision(record, experts, decision)
    decision = _apply_tournament(record, experts, decision)
    threat = evaluate_live_threat(record)
    state = str((memory.get("pressure") or {}).get("state") or "NO_DATA")
    strategy = str(decision.get("strategy") or "")
    active = bool(decision.get("active"))
    probability = decision.get("probability")
    bet = decision.get("status") == "BET" and active

    # Scenarios are OBSERVATIONAL classifications of the original V3 memory,
    # not independent probability generators. Only V3 BET can request pricing.
    scenes = [
        {"id": "L01", "name": "Гол в 1-м тайме",
         "status": decision.get("status") if strategy == "goal_before_ht" else "OUTSIDE_WINDOW"},
        {"id": "L02", "name": "Ещё гол во 2-м тайме",
         "status": decision.get("status") if strategy == "another_goal" else "OUTSIDE_WINDOW"},
        {"id": "L03", "name": "Давление одной команды",
         "status": "MATCH" if state in {"HOME_SIEGE", "AWAY_SIEGE", "HOME_PRESSURE", "AWAY_PRESSURE"} else "WATCH"},
        {"id": "L04", "name": "Обоюдные атаки",
         "status": "MATCH" if state == "END_TO_END" else "WATCH"},
        {"id": "L05", "name": "Ускорение моментов",
         "status": "MATCH" if threat.get("state") in {"SURGE", "BUILDING"} else "WATCH"},
    ]
    result = {
        "brain": "ORIGINAL_BRAIN_V3",
        "decision": "BET" if bet else "NO_BET",
        "status": str(decision.get("status") or "WATCH"),
        "strategy": strategy,
        "market": {
            "goal_before_ht": "GOAL_BEFORE_HT",
            "another_goal": "ANOTHER_GOAL",
        }.get(strategy),
        "probability": probability,
        "bet_min": decision.get("bet_min"),
        "data_quality": quality,
        "pressure_state": state,
        "entry_mode": str(decision.get("entry_mode") or "STANDARD"),
        "full_match_clock": bool(decision.get("full_match_clock")),
        "under_risk": (decision.get("countercase") or {}).get("no_more_goal_risk"),
        "selection_score": (decision.get("selection_audit") or {}).get("score"),
        "selection_verdict": (decision.get("selection_tournament") or {}).get("verdict"),
        "strong_live_qualified": bool((decision.get("strong_live") or {}).get("qualified")),
        "strong_live_activated": bool((decision.get("strong_live") or {}).get("activated")),
        "blocks": list(decision.get("blocks") or []),
        "score_epoch_samples": int((memory.get("score_epoch") or {}).get("samples") or 0),
        "momentum_ready": bool((memory.get("windows") or {}).get("5m")),
        "threat": threat,
        "scenarios": scenes,
    }
    # V3 memory is module-scoped; bound it as strictly as the host match pool.
    if len(_HISTORY) > 64:
        for key in list(_HISTORY)[:len(_HISTORY) - 64]:
            _HISTORY.pop(key, None)
    return result
