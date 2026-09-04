from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from . import signal_worker_all_cards as cards
from .goal_state_engine import build_goal_state_experts
from .goal_state_policy import enforce_goal_state_policy
from .match_context import provider_count, xg_or_proxy_pair
from .multi_autonomous_steam import apply_autonomous_steam
from .multi_journal import settle_multi_journal, sync_multi_journal
from .multi_shadow import analyze_and_record, append_shadow_snapshot, decision_snapshot
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

    Production now has two deliberately separate layers:
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

    # Settlement must run even on the final row or when model/market data is
    # temporarily unavailable; otherwise a valid Multi entry could stay pending.
    settled = settle_multi_journal(record, journal_path)
    if settled:
        emit_multi_results(record, settled)
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

    # One central football brain. Legacy trained models are priors only; team
    # pressure, BTTS, first-half goal and totals are derived from the same state.
    experts = build_goal_state_experts(
        record,
        model_result=model_result,
        two_more_analysis=two_more,
        data_quality=quality,
    )

    # A 1st-half market is closed at the interval even though Flashscore reports
    # minute 45. Never allow a halftime price to revive a closed first-half bet.
    if bool(match.get("is_halftime")):
        experts.pop("goal_before_ht", None)

    market = _market_row(record)
    decision = analyze_and_record(record, market, experts, analysis_path, data_quality=quality)

    # Ordinary GOOL is football-first: PASS may bet; BORDERLINE/NO_DATA need a
    # verified 1xBet confirmation; HARD_NO cannot be revived by VALUE.
    before_key = None if decision.winner is None else decision.winner.key
    decision = enforce_goal_state_policy(decision, experts)

    # Second independent layer: an exceptional fresh 1xBet steam can ignore the
    # GOOL state, but only under its own strict score/freshness/odds/move guards.
    decision = apply_autonomous_steam(decision, record, market, data_quality=quality)
    decision = _enforce_min_rating(decision)
    after_key = None if decision.winner is None else decision.winner.key

    if before_key != after_key:
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
        # Use the exact bookmaker snapshot that produced the decision so the
        # Telegram card keeps real odds from the same score/minute.
        emit_multi_signal(record, decision, created, market_row=market)

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
