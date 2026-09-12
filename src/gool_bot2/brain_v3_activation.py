from __future__ import annotations

from typing import Any, Callable

from .brain_v3_browser_support import apply_browser_support
from .brain_v3_decision_audit import audit_brain_v3_decision
from .brain_v3_external_trends import apply_external_trend_context
from .brain_v3_full_match import (
    apply_full_match_brain_v3_to_experts,
    apply_full_match_strong_live,
    full_match_strategy,
    install_brain_v3_full_match,
)
from .brain_v3_trend_learning import install_brain_v3_trend_learning


_INSTALLED = False
_BRAIN_V3_FORMULA = "Brain V3: LIVE multi-source + Chromium fallback + время/счёт + capped history/trends · 1xBet только кэф"


def install_brain_v3_activation() -> None:
    """Make Brain V3 authoritative for ordinary GOOL without touching STEAM/FLOW.

    The legacy Goal State engine is still calculated for diagnostics and rollback,
    but the active first-half/second-half expert is replaced by Brain V3 before
    1xBet market demand and routing. PREMATCH and historical/browser trends are
    capped support only; the final Decision Audit argues the no-more-goal case and
    compares the current match with other recent BET-quality candidates. 1xBet and
    Matchbook never enter football probability.
    """
    global _INSTALLED
    if _INSTALLED:
        return

    from . import multi_card
    from . import multi_public_metrics as metrics
    from . import multi_runtime as runtime

    metrics.ORDINARY_FORMULA = _BRAIN_V3_FORMULA
    multi_card.ORDINARY_FORMULA = _BRAIN_V3_FORMULA
    install_brain_v3_full_match()
    install_brain_v3_trend_learning()

    original_half: Callable[..., dict[str, Any]] = runtime.apply_half_goal_prior
    original_restore: Callable[..., dict[str, Any]] = runtime._restore_live_only_brain

    def half_then_v3(record: dict[str, Any], experts: dict[str, Any]) -> dict[str, Any]:
        profile = original_half(record, experts)
        quality = runtime._data_quality(record)
        decision = apply_full_match_brain_v3_to_experts(
            record,
            experts,
            data_quality=quality,
            prematch_profile=profile,
        )
        decision = apply_full_match_strong_live(record, experts, decision)
        decision = apply_external_trend_context(record, experts, decision)
        decision = apply_browser_support(record, experts, decision)
        decision = audit_brain_v3_decision(record, experts, decision)
        if bool(decision.get("active")):
            match = record.get("match") or {}
            trend = decision.get("external_trends") or {}
            browser = decision.get("browser365") or {}
            countercase = decision.get("countercase") or {}
            selection = decision.get("selection_audit") or {}
            print(
                f"GOOL_BRAIN_V3 match={match.get('flashscore_event_id') or '-'} "
                f"minute={int(match.get('minute') or 0)} score={int(match.get('home_score') or 0)}:{int(match.get('away_score') or 0)} "
                f"state={decision.get('match_state') or '-'} stage={decision.get('status') or 'WATCH'} "
                f"mode={decision.get('entry_mode') or 'STANDARD'} "
                f"p={float(decision.get('probability') or 0):.3f} "
                f"live={float(decision.get('live_probability') or 0):.3f} "
                f"prematch={float(((decision.get('prematch') or {}).get('adjustment_pp')) or 0):+.1f}pp "
                f"trend={float(trend.get('effective_adjustment_pp') or 0):+.1f}pp "
                f"chrome={float(browser.get('effective_adjustment_pp') or 0):+.1f}pp "
                f"under={float(countercase.get('no_more_goal_risk') or 0):.2f} "
                f"select={float(selection.get('score') or 0):.2f} "
                f"xg5={((decision.get('recent') or {}).get('xg5'))} "
                f"xg10={((decision.get('recent') or {}).get('xg10'))}",
                flush=True,
            )
        return profile

    def restore_v3_probability(
        record: dict[str, Any],
        experts: dict[str, Any],
        intelligence: dict[str, Any],
        live_probabilities: dict[str, float],
    ) -> dict[str, Any]:
        match = record.get("match") or {}
        minute = int(match.get("minute") or 0)
        strategy, _, _ = full_match_strategy(minute, bool(match.get("is_halftime")))
        expert = experts.get(strategy) if strategy else None
        is_v3 = isinstance(expert, dict) and str(expert.get("source") or "").startswith("brain_v3:")
        if not is_v3:
            return original_restore(record, experts, intelligence, live_probabilities)

        v3_probability = float(expert.get("probability") or 0.0)
        probs = dict(live_probabilities)
        probs[strategy] = v3_probability
        out = original_restore(record, experts, intelligence, probs)

        expert["probability"] = round(v3_probability, 4)
        adjustment = dict(out.get("probability_adjustment") or {})
        adjustment.update({
            "strategy": strategy,
            "before": round(v3_probability, 4),
            "after": round(v3_probability, 4),
            "hazard_pp": 0.0,
            "chance_quality_pp": 0.0,
            "kickoff_total_pp": 0.0,
            "lineup_pp": 0.0,
            "total_pp": 0.0,
            "decision_mode": "brain_v3",
            "prematch_decision_enabled": False,
        })
        out["probability_adjustment"] = adjustment
        out["decision_mode"] = "brain_v3"
        out["brain_v3_authoritative"] = True
        record["match_intelligence"] = out
        return out

    runtime.apply_half_goal_prior = half_then_v3
    runtime._restore_live_only_brain = restore_v3_probability
    _INSTALLED = True
    print(
        "GOOL_BRAIN_V3_ACTIVE installed ordinary=authoritative full_match=1-45+46-95 strong_live=guarded_live_only decision_audit=selection+under_countercase live=multi_source+chromium_fallback prematch+trends=capped_support 1xbet=odds_only matchbook=separate",
        flush=True,
    )


__all__ = ["install_brain_v3_activation"]
