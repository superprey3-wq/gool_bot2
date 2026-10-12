"""Conservative, explicitly active GOOL challenger decision gates.

These versions are not calibrated; they MUST use observed bookmaker prices,
strong same-event evidence, and have a quick environment rollback path.
PREMATCH hockey and basketball LIVE can only confirm an existing, market-
priced signal. They do not generate fresh bets from missing market data.
"""
from __future__ import annotations

import math
import os
from typing import Any

from .v4_prematch_engine import PrematchPick


def _threshold(name: str, default: float) -> float:
    try:
        x = float(os.getenv(name, str(default)))
        return x if math.isfinite(x) else default
    except (ValueError, TypeError):
        return default


def _number(value: Any) -> float | None:
    try:
        x = float(value)
        return x if math.isfinite(x) else None
    except (ValueError, TypeError):
        return None


def football_v6_active_picks(
    shadow: dict[str, Any],
    *,
    event_id: str,
    home: str,
    away: str,
    league: str,
    kickoff_ts: float,
) -> tuple[list[PrematchPick], dict[str, Any]]:
    """Select one same-quote, half-goal/BTTS/1X2 candidate, no V5 fallback."""
    base = {
        "model": "football_v6_consensus_active",
        "brain_version": "V6",
        "quote_source": shadow.get("quote_source", "none"),
        "probabilities_calibrated": False,
    }
    if shadow.get("status") != "PASS_SHADOW":
        return [], {**base, "reject": shadow.get("reason", "v6_not_qualified")}
    quality = _number(shadow.get("data_quality"))
    if quality is None or quality < _threshold("GOOL_FOOTBALL_V6_ACTIVE_MIN_QUALITY", .65):
        return [], {**base, "reject": "quality"}
    if _number(kickoff_ts) is None or kickoff_ts <= 0:
        return [], {**base, "reject": "kickoff"}
    eligible = []
    for candidate in shadow.get("candidates") or []:
        if candidate.get("state") != "PASS_SHADOW":
            continue
        market, selection = str(candidate.get("market") or ""), str(candidate.get("selection") or "")
        if market not in {"match_total", "home_total", "away_total", "match_1x2", "btts"}:
            continue
        if market in {"match_total", "home_total", "away_total"}:
            direction, _, line_text = selection.partition(" ")
            line = _number(line_text)
            if direction not in {"over", "under"} or line is None or abs((line % 1.0) - .5) > 1e-9:
                continue
        elif market == "match_1x2" and selection not in {"home", "draw", "away"}:
            continue
        elif market == "btts" and selection not in {"yes", "no"}:
            continue
        odd = _number(candidate.get("odd"))
        p = _number(candidate.get("market_shrunk_probability_heuristic"))
        fair = _number(candidate.get("bookmaker_fair_probability"))
        ev = _number(candidate.get("heuristic_ev_units"))
        disagreement = _number(candidate.get("model_disagreement_pp"))
        if (
            odd is None or odd < 1.4 or
            p is None or not (0.0 < p < 1.0) or
            fair is None or not (0.0 < fair < 1.0) or
            ev is None or ev < _threshold("GOOL_FOOTBALL_V6_ACTIVE_MIN_EV", .07) or
            disagreement is None or disagreement > _threshold("GOOL_FOOTBALL_V6_ACTIVE_MAX_DISAGREEMENT_PP", 10.0) or
            p - fair < .025
        ):
            continue
        pick = PrematchPick(
            str(event_id), str(home), str(away), market, selection,
            odd, p, fair, quality, str(league), float(kickoff_ts),
        )
        eligible.append((ev, -disagreement, pick))
    if not eligible:
        return [], {**base, "reject": "no_live_qualified_candidate"}
    eligible.sort(key=lambda x: (x[0], x[1]), reverse=True)
    best = eligible[0][2]
    return [best], {
        **base,
        "bookmaker": (
            "1xBet" if shadow.get("quote_source") == "1xbet"
            else str(shadow.get("bookmaker") or "Flashscore odds")
        ),
        "v6_unvalidated_consensus": True,
        "v6_gate": "real_odds_quality_agreement_value",
        "v6_consensus_prob_heuristic": best.model_probability,
    }


def hockey_ev_active_signal(
    previous: dict[str, Any] | None,
    ev_shadow: dict[str, Any],
    *,
    odds_range: tuple[float, float] | None = None,
) -> dict[str, Any] | None:
    """Require the V2 side and the push-aware EV side to agree; otherwise WAIT."""
    if not previous or ev_shadow.get("status") != "PASS_SHADOW":
        return None
    if previous.get("direction") != ev_shadow.get("direction"):
        return None
    quality = _number(ev_shadow.get("data_quality"))
    if quality is None or quality < _threshold("GOOL_HOCKEY_EV_ACTIVE_MIN_QUALITY", .65):
        return None
    ev = _number(ev_shadow.get("best_expected_value_units_uncalibrated"))
    minimum = _threshold("GOOL_HOCKEY_EV_ACTIVE_MIN_EV", .10)
    if ev is None or ev < minimum:
        return None
    direction = str(previous.get("direction") or "")
    source = next((x for x in ev_shadow.get("alternatives") or [] if x.get("direction") == direction), None)
    if not source:
        return None
    odd, p, push = (
        _number(previous.get("odd")),
        _number(source.get("conditional_win_probability_uncalibrated")),
        _number(source.get("push_probability")),
    )
    if odd is None or p is None or push is None or not 0 <= push < 1:
        return None
    if abs(odd - float(source["odd"])) > 1e-5 or odd < 1.4 or p < .59:
        return None
    if odds_range and not (odds_range[0] <= odd <= odds_range[1]):
        return None
    return {
        **previous,
        "brain_mode": "hockey_prematch_ev_active",
        "hockey_prematch_ev_shadow": ev_shadow,
        "hockey_ev_confirmed": True,
        "expected_ev_units_uncalibrated": ev,
    }


def _basketball_v4_active_reject_reason(
    dynamic: dict[str, Any] | None,
    historical: dict[str, Any] | None,
    shadow: dict[str, Any],
) -> str | None:
    """Explain why a priced, uncalibrated LIVE V4 candidate must WAIT.

    An absent historical V3 signal is not an agreeing vote. In particular,
    an UNDER based on a short/aggregate stats delta is too fragile to publish:
    require both current-quarter possession data and an independent V3 pick
    priced for the same quarter/line. OVERS can still pass without V3 when
    the production clock, data quality and model gates are satisfied.
    """
    if dynamic is None:
        return "dynamic_missing"
    if shadow.get("state") != "PASS":
        return "shadow_not_pass"
    direction = str(dynamic.get("direction") or "")
    if direction not in {"over", "under"}:
        return "invalid_direction"
    if direction != str(shadow.get("direction") or ""):
        return "shadow_direction_conflict"
    historical_direction = str((historical or {}).get("direction") or "")
    if historical_direction and historical_direction != direction:
        return "v3_direction_conflict"
    if direction == "under" and not historical_direction:
        return "v3_missing_under"

    quality = _number(dynamic.get("data_quality"))
    prob = _number(dynamic.get("model_probability"))
    edge = _number(dynamic.get("edge"))
    strength = _number(dynamic.get("strength"))
    odd = _number(dynamic.get("odd"))
    if None in (quality, prob, edge, strength, odd):
        return "incomplete_dynamic_data"
    if odd < 1.4:
        return "odd_below_minimum"
    if quality < _threshold("GOOL_BASKETBALL_V4_ACTIVE_MIN_QUALITY", .65):
        return "data_quality"
    if prob < _threshold("GOOL_BASKETBALL_V4_ACTIVE_MIN_PROB", .61):
        return "uncalibrated_model_probability"
    if edge < _threshold("GOOL_BASKETBALL_V4_ACTIVE_MIN_EDGE", .075):
        return "model_market_gap"
    if strength < _threshold("GOOL_BASKETBALL_V4_ACTIVE_MIN_STRENGTH", 72.0):
        return "strength"
    if str(dynamic.get("projection_clock_source") or "") != "1xbet_segment_clock":
        return "bookmaker_clock_missing"
    source = str(dynamic.get("possession_source") or "")
    if source == "points_clock_fallback":
        return "possessions_missing"
    if direction == "under":
        if source != "flashscore_current_quarter":
            return "under_unverified_quarter_possessions"
        if quality < _threshold("GOOL_BASKETBALL_V4_UNDER_MIN_QUALITY", .80):
            return "under_low_quality"
        elapsed = _number(dynamic.get("elapsed_seconds"))
        remaining = _number(dynamic.get("remaining_seconds"))
        if elapsed is None or elapsed < _threshold("GOOL_BASKETBALL_V4_UNDER_MIN_ELAPSED", 240.0):
            return "under_short_quarter_sample"
        if remaining is None or remaining < _threshold("GOOL_BASKETBALL_V4_UNDER_MIN_REMAINING", 90.0):
            return "under_endgame_variance"
        # V3's adjusted in-play projection is comparable only for the actual
        # current-quarter market; do not use the full-time prior here.
        h_projected = _number((historical or {}).get("historical_inplay_projected_total"))
        quote = _number(dynamic.get("line"))
        if h_projected is not None and quote is not None and h_projected >= quote:
            return "under_historical_projection_disagrees"
    return None


def basketball_v4_active_signal(
    dynamic: dict[str, Any] | None,
    historical: dict[str, Any] | None,
    shadow: dict[str, Any],
) -> dict[str, Any] | None:
    """Activate a V4 selection only after production data/consensus gates.

    Store a compact reject reason in the existing shadow snapshot. This reuses
    the in-memory per-event diagnostic record; no new unbounded disk log.
    """
    reason = _basketball_v4_active_reject_reason(dynamic, historical, shadow)
    shadow["active_gate"] = "WAIT" if reason else "PASS"
    shadow["active_reject_reason"] = reason
    historical_direction = str((historical or {}).get("direction") or "")
    shadow["v3_status"] = (
        "MISSING" if not historical_direction
        else ("AGREES" if dynamic and historical_direction == dynamic.get("direction") else "CONFLICT")
    )
    if reason:
        return None
    assert dynamic is not None
    return {
        **dynamic,
        "brain_mode": "basketball_live_v4_active",
        "basketball_v4_shadow": shadow,
        "basketball_v3_agrees": historical_direction == str(dynamic.get("direction")),
        "basketball_v4_confirmed": True,
    }


def football_live_v6_veto(shadow: dict[str, Any]) -> str | None:
    """Only hard, observed contradiction can veto an active Brain V3 BET."""
    if shadow.get("status") != "COMPARED":
        return None
    hazard = dict(shadow.get("hazard") or {})
    if not hazard.get("available"):
        return None
    hazard_p = _number(hazard.get("probability"))
    if hazard_p is None:
        return None
    v4 = dict(shadow.get("live_v4") or {})
    v4p = _number(v4.get("probability"))
    # V4 uses a different clock horizon, so do NOT compare probabilities as
    # calibrated votes or reject a high-xG game on a tiny threshold difference.
    if hazard_p < _threshold("GOOL_FOOTBALL_LIVE_V6_VETO_HAZARD_MAX", .33):
        return "football_live_v6_low_goal_hazard"
    if (
        v4p is not None
        and v4.get("data_source") == "provider_xg"
        and v4p < .35
        and hazard_p < .45
    ):
        return "football_live_v6_models_low_goal_chance"
    return None
