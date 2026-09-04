from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from . import signal_worker_all_cards as cards
from .goal_state_engine import build_goal_state_experts
from .goal_state_policy import enforce_goal_state_policy
from .match_context import provider_count, xg_or_proxy_pair
from .multi_autonomous_steam import apply_autonomous_steam
from .multi_confidence_gate import enforce_confidence_gate
from .multi_delivery import finalize_multi_delivery, pending_result_notifications
from .multi_entry_enrichment import enrich_multi_entry
from .multi_journal import settle_multi_journal, sync_multi_journal
from .multi_reentry_guard import enforce_reentry_cooldown
from .multi_router import analyze_multi_match
from .multi_shadow import append_shadow_snapshot, decision_snapshot
from .multi_telegram import emit_multi_results, emit_multi_signal
from .xbet_market_pressure import load_market_state


def _data_quality(record: dict[str, Any]) -> float:
    providers = min(3, provider_count(record))
    provider_score = providers / 3.0

    try:
        _, _, xg_source, xg_evidence = xg_or_proxy_pair(record)
    except Exception:
        xg_source, xg_evidence = "unavailable", 0
    xg_score = 1.0 if xg_source == "provider_xg" else (0.65 if xg_source == "attack_proxy" and xg_evidence >= 2 else 0.0)

    momentum = record.get("live_momentum") or {}
    momentum_keys = (
        "shots_total_last_5m", "sot_total_last_5m", "xg_total_last_5m",
        "shots_total_last_10m", "sot_total_last_10m", "xg_total_last_10m",
    )
    available = sum(1 for key in momentum_keys if momentum.get(key) is not None)
    momentum_score = available / len(momentum_keys)

    quality = 0.48 * provider_score + 0.27 * xg_score + 0.25 * momentum_score
    return max(0.0, min(1.0, quality))


def _market_row(record: dict[str, Any]) -> dict[str, Any] | None:
    mid = str(((record.get("match") or {}).get("flashscore_event_id") or ""))
    if not mid:
        return None
    return ((load_market_state().get("matches") or {}).get(mid) or None)


def _paths() -> tuple[Path, Path]:
    runtime = Path(os.getenv("RUNTIME_DATA_DIR", "data"))
    analysis_raw = os.getenv("GOOL_MULTI_ANALYSIS_PATH", "").strip()
    if not analysis_raw:
        analysis_raw = os.getenv("GOOL_MULTI_SHADOW_PATH", "").strip()
    analysis = Path(analysis_raw) if analysis_raw else runtime / "live" / "gool_multi_analysis.jsonl"
    journal_raw = os.getenv("GOOL_MULTI_JOURNAL_PATH", "").strip()
    journal = Path(journal_raw) if journal_raw else runtime / "live" / "gool_multi_journal.json"
    return analysis, journal


def _minimum_rating() -> float:
    try:
        return max(0.0, min(100.0, float(os.getenv("GOOL_MULTI_MIN_RATING", "70"))))
    except (TypeError, ValueError):
        return 70.0


def _enforce_min_rating(decision: Any) -> Any:
    """Final production gate: never journal/send a Multi BET below the floor."""
    if str(getattr(decision, "status", "")) != "BET" or getattr(decision, "winner", None) is None:
        return decision

    floor = _minimum_rating()
    winner = decision.winner
    if float(getattr(winner, "rating", 0.0) or 0.0) >= floor:
        return decision

    if "goal_state_rating_below_70" not in winner.blocks:
        winner.blocks.append("goal_state_rating_below_70")
    winner.reason_tags.append("production_rating_floor")
    winner.eligible = False
    if not any(row.key == winner.key for row in decision.rejected):
        decision.rejected.append(winner)

    decision.status = "WAIT"
    decision.winner = None
    decision.alternatives = []
    decision.reason = f"WAIT: финальный рейтинг ниже {floor:.0f}/100 — реальную ставку не отправляем."
    return decision


def observe_multi_shadow(worker: Any, record: dict[str, Any]) -> None:
    """Feed one production snapshot into GOOL MULTI.

    Production has two deliberately separate layers:
    1) GOOL Goal State — football-first decision from one coherent LIVE state;
    2) autonomous 1xBet steam — exceptional market-only bypass with hard guards.

    The bookmaker remains mandatory for the actual tradable market and price.
    """
    match = record.get("match") or {}
    mid = str(match.get("flashscore_event_id") or "")
    minute = int(match.get("minute") or 0)
    if not mid:
        return

    analysis_path, journal_path = _paths()

    settled = settle_multi_journal(record, journal_path)
    result_rows = pending_result_notifications(journal_path, match_id=mid)
    if result_rows:
        emit_multi_results(record, result_rows, journal_path=journal_path)
    for row in settled:
        print(
            f"GOOL_MULTI_SETTLED match={mid} market={row.get('market')} result={row.get('result')} "
            f"profit={row.get('profit_units')} score={row.get('settled_score')}",
            flush=True,
        )

    if minute <= 0 or bool(match.get("is_finished")):
        return

    model_result = dict(getattr(worker, "_diag_model_result", {}) or {})
    two_more = dict(cards._LAST_TWO_MORE.get(mid) or {})
    quality = _data_quality(record)

    experts = build_goal_state_experts(
        record,
        model_result=model_result,
        two_more_analysis=two_more,
        data_quality=quality,
    )

    if bool(match.get("is_halftime")):
        experts.pop("goal_before_ht", None)

    market = _market_row(record)
    decision = analyze_multi_match(match, market, experts, data_quality=quality)

    decision = enforce_goal_state_policy(decision, experts)

    # Strong 1xBet confirmation now includes breadth across related markets.
    # It may slightly support an already-good GOOL idea, but never replaces
    # the strategy-specific football confidence floor.
    decision = enforce_confidence_gate(decision, experts, market_row=market)

    # Autonomous STEAM is still a separate exceptional layer. It now requires
    # related-market breadth unless the target move itself is extreme.
    decision = apply_autonomous_steam(decision, record, market, data_quality=quality)
    decision = _enforce_min_rating(decision)

    decision = enforce_reentry_cooldown(decision, record, journal_path)

    append_shadow_snapshot(
        analysis_path,
        decision_snapshot(record, decision, experts, data_quality=quality, market_row=market),
    )

    _, created = sync_multi_journal(
        record,
        decision,
        experts,
        journal_path,
        data_quality=quality,
    )
    if created is not None:
        created = enrich_multi_entry(
            journal_path,
            created,
            record,
            decision,
            experts,
            data_quality=quality,
        )
        sent = emit_multi_signal(record, decision, created, market_row=market)
        finalized = finalize_multi_delivery(journal_path, created, sent)
        if str(created.get("mode") or "").lower() == "active":
            print(
                f"GOOL_MULTI_DELIVERY match={mid} sent={sent} "
                f"journal={'kept' if sent > 0 else 'discarded'} finalized={int(finalized)} "
                f"confidence={created.get('confidence_score')} layer={created.get('layer')}",
                flush=True,
            )

    winner = None if decision.winner is None else f"{decision.winner.label}@{decision.winner.odd:.2f}"
    source = None if decision.winner is None else (
        "STEAM_OVERRIDE" if str(decision.winner.source).startswith("1xbet:autonomous_steam")
        else "MARKET_CONFIRM" if decision.winner.market_override and not decision.winner.expert_passed
        else "GOAL_STATE"
    )
    print(
        f"GOOL_MULTI_SHADOW match={mid} minute={minute} score={match.get('home_score',0)}:{match.get('away_score',0)} "
        f"decision={decision.status} best={winner or '-'} source={source or '-'} quality={quality:.2f} "
        f"journal_entry={'yes' if created else 'no'}",
        flush=True,
    )
