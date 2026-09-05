from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from . import signal_worker_all_cards as cards
from .goal_state_engine import build_goal_state_experts
from .goal_state_policy import enforce_goal_state_policy
from .match_context import provider_count, xg_or_proxy_pair
from .matchbook_exchange import matchbook_context
from .multi_another_goal_guard import enforce_another_goal_context
from .multi_autonomous_steam import apply_autonomous_steam
from .multi_concept import enforce_entry_cutoff, routing_experts
from .multi_delivery import finalize_multi_delivery, pending_result_notifications
from .multi_entry_enrichment import enrich_multi_entry
from .multi_exchange_confirmation import apply_matchbook_confirmation
from .multi_journal import settle_multi_journal, sync_multi_journal
from .multi_lineup_context import apply_lineup_context
from .multi_match_intelligence import apply_match_intelligence, enforce_match_suitability
from .multi_reentry_guard import enforce_reentry_cooldown
from .multi_router import analyze_multi_match
from .multi_shadow import append_shadow_snapshot, decision_snapshot
from .multi_telegram import emit_multi_results, emit_multi_signal
from .multi_true_prematch import apply_true_prematch_market
from .prematch_goal_profile import apply_half_goal_prior
from .xbet_market_demand import request_live_market
from .xbet_market_pressure import live_1x2_context, load_market_state


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



def _ensure_any_goal_coverage_proxy(experts: dict[str, Any]) -> None:
    """Backwards-compatible coverage proxy used only when broad any-goal is absent.

    The current Goal State engine normally emits `another_goal` directly. Older
    or partial expert payloads can contain only a passed home/away goal expert;
    preserve that historical fallback without replacing a calibrated broad model.
    """
    if "another_goal" in experts:
        return
    candidates: list[tuple[str, dict[str, Any]]] = []
    for key in ("home_goal", "away_goal"):
        row = experts.get(key)
        if not isinstance(row, dict) or not bool(row.get("passed")):
            continue
        try:
            probability = float(row.get("probability"))
        except (TypeError, ValueError):
            continue
        candidates.append((key, {**row, "probability": probability}))
    if not candidates:
        return
    key, selected = max(candidates, key=lambda item: float(item[1].get("probability") or 0.0))
    experts["another_goal"] = {
        **selected,
        "coverage_proxy": True,
        "proxy_from": key,
    }


def observe_multi_shadow(worker: Any, record: dict[str, Any]) -> None:
    """Feed one production snapshot into GOOL MULTI.

    Production has two deliberately separate layers:
    1) ordinary GOOL: goal before HT in the first half, another goal in the
       second half through 75';
    2) autonomous 1xBet steam: exceptional market-only bypass with hard guards.

    Only ordinary GOOL uses the 35'/75' entry windows; autonomous market
    systems remain live for the whole match while their markets are tradable.
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
    _ensure_any_goal_coverage_proxy(experts)

    half_profile = apply_half_goal_prior(record, experts)
    active_prior = half_profile.get("active") or {}
    if active_prior.get("available"):
        print(
            f"GOOL_HALF_PREMATCH match={mid} period={active_prior.get('period')} "
            f"line={active_prior.get('next_total_line')} p_next={active_prior.get('one_more_probability')} "
            f"sample={active_prior.get('pair_sample')} h2h={active_prior.get('h2h_sample')}",
            flush=True,
        )

    demand = request_live_market(record, experts)
    if demand is not None:
        print(
            f"XBET_DEMAND_REQUEST match={mid} minute={minute} strategy={demand.get('strategy')} "
            f"state={demand.get('football_state')} p={float(demand.get('football_probability') or 0):.3f}",
            flush=True,
        )

    if bool(match.get("is_halftime")):
        experts.pop("goal_before_ht", None)

    market = _market_row(record)
    record["xbet_live_1x2"] = live_1x2_context(market)
    record["matchbook_exchange"] = matchbook_context(record)
    intelligence = apply_match_intelligence(
        record,
        experts,
        market,
        data_quality=quality,
    )
    true_prematch = apply_true_prematch_market(record, experts)
    lineup = apply_lineup_context(record, experts)
    if true_prematch or lineup:
        intelligence = record.get("match_intelligence") or intelligence

    suitability = intelligence.get("suitability") or {}
    epoch = intelligence.get("score_epoch") or {}
    chance = intelligence.get("chance_quality") or {}
    adjustment = intelligence.get("probability_adjustment") or {}
    kickoff = intelligence.get("kickoff_market") or {}
    lineup_risk = suitability.get("lineup_risk") or "unknown"
    print(
        f"GOOL_MATCH_INTELLIGENCE match={mid} suitability={float(suitability.get('score') or 0):.2f} "
        f"epoch={epoch.get('identity') or '-'} epoch_min={epoch.get('minutes')} "
        f"chance={float(chance.get('score') or 0.5):.2f} adjust_pp={float(adjustment.get('total_pp') or 0):+.1f} "
        f"kickoff={kickoff.get('quality') or 'none'} lineup={lineup_risk}",
        flush=True,
    )

    exchange = record.get("matchbook_exchange") or {}
    active_strategy = "goal_before_ht" if 1 <= minute <= 35 else "another_goal" if 46 <= minute <= 75 else ""
    exchange_active = ((exchange.get("systems") or {}).get(active_strategy) or {}) if active_strategy else {}
    if exchange.get("available"):
        print(
            f"GOOL_MATCHBOOK match={mid} mapped={float(exchange.get('match_score') or 0):.2f} "
            f"event={((exchange.get('event') or {}).get('id') or '-')} strategy={active_strategy or '-'} "
            f"line={exchange_active.get('line')} level={exchange_active.get('level') or 'NO_MARKET'} "
            f"volume={float(exchange_active.get('volume') or 0):.0f} "
            f"delta_pp={float(((exchange_active.get('flow') or {}).get('direction_pp')) or 0):+.2f}",
            flush=True,
        )

    ordinary_experts = routing_experts(match, experts)
    decision = analyze_multi_match(match, market, ordinary_experts, data_quality=quality)

    decision = enforce_goal_state_policy(decision, experts)

    # Matchbook cannot manufacture an ordinary GOOL BET. It can only annotate
    # or modestly adjust a candidate that the single football Brain already chose.
    decision = apply_matchbook_confirmation(decision, record)

    # Do not chase a just-realized goal. For tied/high-scoring states, live 1X2
    # draw repricing is explicit opposition unless football + total market are
    # both unusually strong.
    decision = enforce_another_goal_context(decision, record, experts, market)

    # Ordinary GOOL must also pass whole-match suitability. This gate is placed
    # before autonomous STEAM so the separate steam system keeps its own guards.
    decision = enforce_match_suitability(decision, record)

    # Autonomous STEAM remains a separate exceptional layer, but the concept's
    # global 75' entry deadline is applied immediately after it.
    decision = apply_autonomous_steam(decision, record, market, data_quality=quality)
    decision = enforce_entry_cutoff(decision)

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
