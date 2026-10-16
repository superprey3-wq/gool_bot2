"""Conservative ACTIVE PREMATCH scenario fallback.

Only when the production Brain has no public-priced pick:
* recognise a documented MATCH scenario before kickoff;
* require a real current bookmaker lane with explicit settlement;
* independently re-price the same side using existing sport models;
* shrink the uncalibrated estimate toward the de-vigged market;
* retain every production dedup/delivery/price guard.
Never treat mere scenario MATCH as a valid probability.
"""
from __future__ import annotations

import math
import os
from typing import Any

from .prematch_strategy_scenarios import _match_target
from .single_price_policy import public_single_allowed


def _n(x: Any) -> float | None:
    try:
        n = float(x)
    except (TypeError, ValueError):
        return None
    return n if math.isfinite(n) else None


def active_enabled() -> bool:
    return str(os.getenv("GOOL_PREMATCH_SCENARIO_ACTIVE", "1")).casefold() in {"1", "true", "yes", "on"}


_SUPPORTED = {
    "hockey": {"H01", "H02", "H07"},
    "basketball": {"B01", "B02", "B03", "B04", "B05", "B06"},
    "football": {"F01", "F02", "F03", "F04", "F05"},
}


def matching_scenario_ids(
    snapshot: dict[str, Any],
    *,
    sport: str,
    family: str,
    direction: str,
    scope: str = "FULL_MATCH",
    line: float | None = None,
) -> list[str]:
    """No guess about directions or market scopes; positive handicap must be +."""
    ids: list[str] = []
    for scenario in snapshot.get("scenarios") or []:
        if not isinstance(scenario, dict):
            continue
        code = str(scenario.get("id") or "")
        if code not in _SUPPORTED.get(sport, set()) or scenario.get("status") != "MATCH":
            continue
        if code == "H07" and scope == "FULL_MATCH":
            continue
        if code == "B04" and (family != "handicap" or line is None or line <= 0):
            continue
        if code == "B03" and family == "handicap" and (line is None or line >= 0):
            continue
        option = {"family": family, "direction": direction, "scope": scope}
        if any(_match_target(target, option) for target in scenario.get("market_targets") or []):
            ids.append(code)
    return ids


def recover_multisport_prematch(
    *,
    sport: str,
    scenario_snapshot: dict[str, Any],
    lanes: list[dict[str, Any]],
    features: dict[str, Any],
    league: str,
    segment_memory: dict[str, Any] | None = None,
) -> tuple[list[tuple[dict[str, Any], dict[str, Any]]], dict[str, int]]:
    """Return at most one vetted alternative to a production WAIT, not an extra bet."""
    diag: dict[str, int] = {"lanes_checked": 0, "matching": 0, "qualified": 0}
    if not active_enabled() or sport not in {"hockey", "basketball"}:
        return [], diag
    if min(_n(features.get("home_recent_n")) or 0, _n(features.get("away_recent_n")) or 0) < 8:
        return [], diag
    from .hockey_brain_v2 import _data_quality as hockey_quality, _side_probabilities
    from .hockey_prematch_ev_shadow import evaluate_hockey_prematch_ev_shadow
    from .basketball_brain_v2 import prematch_signal as basketball_v2_prematch
    from .basketball_brain_v2 import _data_quality as basketball_quality

    quality = hockey_quality(features) if sport == "hockey" else basketball_quality(features)
    if quality < (0.75 if sport == "hockey" else 0.73):
        return [], diag
    memory = dict(segment_memory or {})
    out: list[tuple[float, dict[str, Any], dict[str, Any]]] = []
    for row in lanes:
        diag["lanes_checked"] += 1
        family = str(row.get("market_family") or "")
        scope = str(row.get("scope") or "FULL_MATCH")
        line = _n(row.get("line"))
        if family not in {"match_total", "home_total", "away_total", "handicap"} or line is None:
            continue
        if sport == "hockey" and family == "handicap":
            # Existing handicap quarantine is deliberately preserved.
            continue
        if sport == "hockey" and family in {"home_total", "away_total"} and scope == "FULL_MATCH" and line >= 7.5:
            continue
        if scope != "FULL_MATCH":
            if (_n(memory.get("quality")) or 0) < 0.65:
                continue
            if sport == "hockey" and scope not in {"PERIOD_1", "PERIOD_2", "PERIOD_3"}:
                continue
            if sport == "basketball" and scope not in {
                "QUARTER_1", "QUARTER_2", "QUARTER_3", "QUARTER_4",
                "FIRST_HALF", "SECOND_HALF",
            }:
                continue

        if sport == "hockey":
            ev = evaluate_hockey_prematch_ev_shadow(row, features, league, memory)
            if ev.get("status") != "PASS_SHADOW":
                continue
            direction = str(ev.get("direction") or "")
            if direction not in {"over", "under"}:
                continue
            ids = matching_scenario_ids(
                scenario_snapshot, sport=sport, family=family,
                direction=direction, scope=scope, line=line,
            )
            if not ids:
                continue
            diag["matching"] += 1
            option = next((r for r in ev.get("alternatives") or [] if r.get("direction") == direction), None)
            if not option:
                continue
            odd = _n(option.get("odd"))
            raw_p = _n(option.get("conditional_win_probability_uncalibrated"))
            market_over = _n(row.get("probability"))
            push = _n(option.get("push_probability"))
            mu = _n(ev.get("lambda_scoped"))
            if None in (odd, raw_p, market_over, push, mu) or not (0 < market_over < 1):
                continue
            if not public_single_allowed(odd, phase="PREMATCH", sport=sport) or odd > 3.0:
                continue
            if raw_p < 0.62 or (_n(ev.get("best_expected_value_units_uncalibrated")) or 0) < .12:
                continue
            # Stress the full push-aware payoff, not just P(win|decisive).
            ev_low = _side_probabilities(mu * .85, line, direction)
            ev_high = _side_probabilities(mu * 1.15, line, direction)
            stressed = min(w * (odd - 1) - l for w, _, l in (ev_low, ev_high))
            if stressed < -.13:
                continue
            fair = market_over if direction == "over" else 1.0 - market_over
            model = fair + .52 * max(0.0, raw_p - fair)
            net_ev = (1 - push) * (model * odd - 1.0)
            if model < .60 or net_ev < .035:
                continue
            signal = {
                "phase": "PREMATCH",
                "brain_mode": "hockey_prematch_scenario_active",
                "direction": direction,
                "line": line,
                "odd": odd,
                "model_probability": round(model, 6),
                "market_probability": round(fair, 6),
                "fair_probability": round(model, 6),
                "edge": round(model - fair, 6),
                "value_after_vig_proxy": round(net_ev, 6),
                "expected_ev_units_uncalibrated": round(net_ev, 6),
                "data_quality": round(quality, 3),
                "strength": round(min(82.0, 55.0 + quality * 16 + net_ev * 40), 1),
                "push_probability": push,
                "hockey_prematch_ev_shadow": ev,
                "scenario_activation": {
                    "scenario_ids": ids,
                    "recovery_from": "production_wait",
                    "probabilities_calibrated": False,
                    "market_shrinkage": .52,
                    "worst_15pct_goal_rate_ev_uncalibrated": round(stressed, 5),
                },
            }
        else:
            # V2's independent model gates (quality, minimum edge, actual
            # book quote, recent history agreement) remain mandatory.
            dynamic = basketball_v2_prematch(row, features, league, memory)
            if not dynamic:
                continue
            direction = str(dynamic.get("direction") or "")
            ids = matching_scenario_ids(
                scenario_snapshot, sport=sport, family=family,
                direction=direction, scope=scope, line=line,
            )
            if not ids:
                continue
            diag["matching"] += 1
            odd = _n(dynamic.get("odd"))
            raw_p = _n(dynamic.get("model_probability"))
            fair = _n(dynamic.get("market_probability"))
            if None in (odd, raw_p, fair) or not (0 < fair < 1):
                continue
            if not public_single_allowed(odd, phase="PREMATCH", sport=sport) or odd > 3.0:
                continue
            if raw_p - fair < .09:
                continue
            if family == "handicap" and scope != "FULL_MATCH":
                continue
            # Distinguish quarters/halves/full match, and reject the extreme
            # historical mismatch that previously generated false confidence.
            mu = _n(dynamic.get("mu_total"))
            gap = abs(mu - line) if mu is not None and family == "match_total" else 0.0
            maximum = 5.0 if scope.startswith("QUARTER_") else 9.0 if scope.endswith("_HALF") else 18.0
            if gap > maximum:
                continue
            if family == "handicap" and raw_p < .67:
                continue
            model = fair + .55 * max(0.0, raw_p - fair)
            net_ev = model * odd - 1.0
            if model < .58 or net_ev < (.055 if family == "handicap" else .035):
                continue
            signal = {
                **dynamic,
                "brain_mode": "basketball_prematch_scenario_active",
                "model_probability": round(model, 6),
                "fair_probability": round(model, 6),
                "edge": round(model - fair, 6),
                "value_after_vig_proxy": round(net_ev, 6),
                "expected_ev_units_uncalibrated": round(net_ev, 6),
                "scenario_activation": {
                    "scenario_ids": ids,
                    "recovery_from": "production_wait",
                    "probabilities_calibrated": False,
                    "market_shrinkage": .55,
                },
            }
        diag["qualified"] += 1
        out.append((float(signal["value_after_vig_proxy"]), row, signal))

    if not out:
        return [], diag
    # At most one recovery per fixture; no duplicated same-game bets.
    out.sort(key=lambda item: (item[0], float(item[2].get("data_quality") or 0)), reverse=True)
    _, row, signal = out[0]
    return [(row, signal)], diag


def recover_football_v6_prematch(
    scenarios: dict[str, Any],
    *,
    tournament_candidates: list[Any],
    shadow: dict[str, Any],
) -> tuple[list[Any], dict[str, Any]]:
    """Promote only V6's already independently-vetted near-misses.

    We do not change V6's regular selection: this fallback applies only on WAIT,
    and does not infer unquoted handicaps or goal probabilities.
    """
    if not active_enabled() or shadow.get("quote_source") not in {"1xbet", "flashscore"}:
        return [], {}
    quality = _n(shadow.get("data_quality"))
    if quality is None or quality < .70:
        return [], {}
    ranked = []
    for pick in tournament_candidates:
        odd, p, fair = _n(pick.odds), _n(pick.model_probability), _n(pick.market_probability)
        if None in (odd, p, fair) or odd > 3.0 or not public_single_allowed(odd, phase="PREMATCH", sport="football"):
            continue
        if p < .60 or p - fair < .023 or pick.expected_value < .045:
            continue
        direction = pick.selection.partition(" ")[0] if pick.market in {"match_total", "home_total", "away_total"} else pick.selection
        ids = matching_scenario_ids(
            scenarios, sport="football", family=pick.market, direction=direction,
        )
        if not ids:
            continue
        ranked.append((pick.expected_value, pick, ids))
    if not ranked:
        return [], {}
    ranked.sort(key=lambda x: x[0], reverse=True)
    _, chosen, ids = ranked[0]
    return [chosen], {
        "model": "football_v6_prematch_scenario_active",
        "bookmaker": "1xBet" if shadow.get("quote_source") == "1xbet" else "Flashscore odds",
        "scenario_ids": ids,
        "uncalibrated": True,
        "recovery_from": "v6_wait",
        "source": "v6_tournament_consensus_real_odds",
    }
