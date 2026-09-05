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


def _live_only() -> bool:
    """Keep PREMATCH collection/diagnostics but exclude it from GOOL decisions by default."""
    raw = str(os.getenv("GOOL_LIVE_ONLY", "1")).strip().casefold()
    return raw not in {"0", "false", "no", "off"}


def _live_only_brain_inputs(
    record: dict[str, Any],
    model_result: dict[str, Any],
    two_more: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    """Return a decision-only view with historical/prematch priors removed.

    The original record is deliberately left untouched so PREMATCH data can still
    be collected, displayed and written to diagnostics. The Goal State brain sees
    only LIVE football state plus the dedicated live-pressure model.
    """
    if not _live_only():
        return record, model_result, two_more

    live_record = dict(record)
    live_record["prematch_context"] = {}
    live_record["prematch_goal_profile"] = {}
    live_record.pop("xbet_prematch_market", None)

    live_model = dict(model_result)
    live_model.pop("trained_probability", None)
    live_model.pop("direct", None)

    # The ordinary production concept no longer routes the legacy +2 head, but
    # clearing it here also prevents a historical helper from leaking into the
    # unified Goal State diagnostics.
    return live_record, live_model, {}


def _expert_probabilities(experts: dict[str, Any]) -> dict[str, float]:
    out: dict[str, float] = {}
    for key, row in experts.items():
        if not isinstance(row, dict):
            continue
        try:
            out[str(key)] = float(row.get("probability"))
        except (TypeError, ValueError):
            continue
    return out


def _restore_live_only_brain(
    record: dict[str, Any],
    experts: dict[str, Any],
    intelligence: dict[str, Any],
    live_probabilities: dict[str, float],
) -> dict[str, Any]:
    """Remove every PREMATCH/lineup probability and suitability contribution.

    PREMATCH enrichers still run before this function so their snapshots remain
    available for Telegram diagnostics and later research. Immediately before
    routing, the active expert and match-suitability score are rebuilt only from
    LIVE inputs.
    """
    if not _live_only():
        return intelligence

    adjustment = intelligence.get("probability_adjustment") or {}
    strategy = str(adjustment.get("strategy") or "")
    live_before = live_probabilities.get(strategy)
    expert = experts.get(strategy) if strategy else None

    if live_before is not None and isinstance(expert, dict):
        try:
            hazard_pp = float(adjustment.get("hazard_pp") or 0.0)
        except (TypeError, ValueError):
            hazard_pp = 0.0
        try:
            chance_pp = float(adjustment.get("chance_quality_pp") or 0.0)
        except (TypeError, ValueError):
            chance_pp = 0.0

        # Preserve the same global adjustment cap as Match Intelligence, but only
        # LIVE minute-hazard and LIVE chance quality are allowed to contribute.
        total_pp = max(-4.0, min(4.0, hazard_pp + chance_pp))
        adjusted = max(0.01, min(0.99, float(live_before) + total_pp / 100.0))
        expert["probability"] = round(adjusted, 4)

        adjustment.update({
            "before": round(float(live_before), 4),
            "after": round(adjusted, 4),
            "kickoff_total_pp": 0.0,
            "lineup_pp": 0.0,
            "total_pp": round(total_pp, 2),
            "prematch_decision_enabled": False,
            "decision_mode": "live_only",
        })

        diagnostics = dict(expert.get("diagnostics") or {})
        diagnostics["live_only_brain"] = {
            "enabled": True,
            "live_probability_before": round(float(live_before), 4),
            "live_probability_after": round(adjusted, 4),
            "hazard_pp": round(hazard_pp, 2),
            "chance_quality_pp": round(chance_pp, 2),
            "prematch_probability_contribution_pp": 0.0,
            "kickoff_probability_contribution_pp": 0.0,
            "lineup_probability_contribution_pp": 0.0,
        }
        if isinstance(diagnostics.get("half_prematch_prior"), dict):
            diagnostics["half_prematch_prior"]["decision_enabled"] = False
        match_intel_diag = dict(diagnostics.get("match_intelligence") or {})
        if match_intel_diag:
            match_intel_diag["kickoff_prior_used"] = False
            match_intel_diag["prematch_decision_enabled"] = False
            match_intel_diag["probability_before"] = round(float(live_before), 4)
            match_intel_diag["probability_after"] = round(adjusted, 4)
            match_intel_diag["delta_pp"] = round(total_pp, 2)
            diagnostics["match_intelligence"] = match_intel_diag
        expert["diagnostics"] = diagnostics

    suitability = intelligence.get("suitability") or {}
    components = suitability.get("components") or {}
    if isinstance(components, dict):
        # Preserve the relative weights of the four existing LIVE components:
        # 0.22 integrity, 0.18 provider data, 0.18 live market, 0.16 epoch.
        # Their original total is 0.74, so normalize them to 1.0 rather than
        # inventing a looser threshold.
        base_weights = {
            "integrity": 0.22,
            "provider_data": 0.18,
            "market": 0.18,
            "epoch_evidence": 0.16,
        }
        total_weight = sum(base_weights.values())
        live_weights = {key: value / total_weight for key, value in base_weights.items()}

        score = 0.0
        for key, weight in live_weights.items():
            try:
                component = float(components.get(key) or 0.0)
            except (TypeError, ValueError):
                component = 0.0
            score += max(0.0, min(1.0, component)) * weight

        suitability["score"] = round(max(0.0, min(1.0, score)), 4)
        suitability["decision_mode"] = "live_only"
        suitability["decision_weights"] = {key: round(value, 4) for key, value in live_weights.items()}
        suitability["disabled_decision_components"] = ["history", "kickoff", "lineup"]

    intelligence["probability_adjustment"] = adjustment
    intelligence["suitability"] = suitability
    intelligence["decision_mode"] = "live_only"
    intelligence["prematch_decision_enabled"] = False
    record["match_intelligence"] = intelligence
    return intelligence


def _enforce_another_goal_context_for_mode(
    decision: Any,
    record: dict[str, Any],
    experts: dict[str, Any],
    market: dict[str, Any] | None,
) -> Any:
    """Run the live guard while hiding historical half-profile in LIVE-only mode."""
    if not _live_only():
        return enforce_another_goal_context(decision, record, experts, market)

    sentinel = object()
    saved = record.get("prematch_goal_profile", sentinel)
    record["prematch_goal_profile"] = {}
    try:
        return enforce_another_goal_context(decision, record, experts, market)
    finally:
        if saved is sentinel:
            record.pop("prematch_goal_profile", None)
        else:
            record["prematch_goal_profile"] = saved


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

    With GOOL_LIVE_ONLY=1 (the default), all PREMATCH/history/lineup/kickoff
    context is collected for diagnostics only and cannot alter an ordinary
    GOOL BET/WAIT decision.
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

    brain_record, brain_model_result, brain_two_more = _live_only_brain_inputs(
        record,
        model_result,
        two_more,
    )
    experts = build_goal_state_experts(
        brain_record,
        model_result=brain_model_result,
        two_more_analysis=brain_two_more,
        data_quality=quality,
    )
    _ensure_any_goal_coverage_proxy(experts)
    live_probabilities = _expert_probabilities(experts)

    # Keep PREMATCH in the bot for collection, research and diagnostics. In
    # LIVE-only mode any probability changes made by these enrichers are reset
    # immediately before routing.
    half_profile = apply_half_goal_prior(record, experts)
    active_prior = half_profile.get("active") or {}
    if active_prior.get("available"):
        print(
            f"GOOL_HALF_PREMATCH match={mid} period={active_prior.get('period')} "
            f"line={active_prior.get('next_total_line')} p_next={active_prior.get('one_more_probability')} "
            f"sample={active_prior.get('pair_sample')} h2h={active_prior.get('h2h_sample')} "
            f"decision={'off' if _live_only() else 'on'}",
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

    intelligence = _restore_live_only_brain(
        record,
        experts,
        intelligence,
        live_probabilities,
    )

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
        f"kickoff={kickoff.get('quality') or 'none'} lineup={lineup_risk} "
        f"brain={'LIVE_ONLY' if _live_only() else 'HYBRID'}",
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

    # Do not chase a just-realized goal. LIVE 1X2 remains valid opposition.
    # In LIVE-only mode the historical second-half total saturation branch is
    # deliberately hidden from this guard.
    decision = _enforce_another_goal_context_for_mode(decision, record, experts, market)

    # Ordinary GOOL must also pass whole-match suitability. In LIVE-only mode
    # this score has already been rebuilt from integrity/provider/LIVE-market/
    # score-epoch evidence only.
    decision = enforce_match_suitability(decision, record)

    # Autonomous STEAM is a separate all-LIVE market hunter. The cutoff below
    # applies only to ordinary GOOL; enforce_entry_cutoff explicitly bypasses STEAM.
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
        f"journal_entry={'yes' if created else 'no'} brain={'LIVE_ONLY' if _live_only() else 'HYBRID'}",
        flush=True,
    )
