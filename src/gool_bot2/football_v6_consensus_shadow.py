"""GOOL Football V6 PREMATCH consensus shadow: no Telegram publication.

Combine the opponent-adjusted V5 rate forecast with an independently
constructed history profile. Compare on the SAME real bookmaker prices.
Dixon-Coles is a low-score sensitivity check, NOT a third independent model.
The conservative bookmaker shrinkage is a heuristic until a chronological
out-of-sample dataset supports fitted calibration.
"""
from __future__ import annotations

import math
from typing import Any

from .football_model_challengers import (
    poisson_profile_challenger,
    dixon_coles_profile_challenger,
    total_probability,
)
from .football_v5_shadow import score_grid
from .v4_prematch_engine import devig_two_way, devig_three_way


def _finite(x: Any) -> float | None:
    try:
        value = float(x)
    except (ValueError, TypeError):
        return None
    return value if math.isfinite(value) else None


def _poisson_over(total_lambda: float, line: float) -> float | None:
    if (
        not math.isfinite(line)
        or line < 0.5
        or abs(line % 1.0 - 0.5) > 1e-9
    ):
        # Asian whole/quarter totals require proper push/half-odds settlement.
        return None
    threshold = math.floor(line) + 1
    cdf = sum(
        math.exp(-total_lambda) * total_lambda ** k / math.factorial(k)
        for k in range(threshold)
    )
    return min(1.0, max(0.0, 1.0 - cdf))


def evaluate_football_v6_prematch(
    forecast_v5: dict[str, Any],
    profile: dict[str, Any],
    xbet: dict[str, Any] | None,
    *,
    event_id: str,
    active_picks: list[Any] | None = None,
) -> dict[str, Any]:
    """Compare independent historical vs V5 probabilities before kickoff."""
    base: dict[str, Any] = {
        "model": "football_v6_consensus_shadow",
        "status": "WAIT",
        "event_id": str(event_id),
        "probabilities_calibrated": False,
        "published": False,
        "active_v5_count": len(active_picks or []),
    }
    if str(forecast_v5.get("status") or "") != "READY":
        return {**base, "reason": "v5_history_unavailable"}
    quote = xbet if isinstance(xbet, dict) else {}
    try:
        hl, al = float(forecast_v5["home_lambda"]), float(forecast_v5["away_lambda"])
        quality = float(forecast_v5.get("quality") or 0)
    except (TypeError, KeyError, ValueError):
        return {**base, "reason": "v5_lambda_unavailable"}
    if not (math.isfinite(hl) and math.isfinite(al) and hl > 0 and al > 0):
        return {**base, "reason": "v5_lambda_invalid"}
    if quality < 0.55:
        return {**base, "reason": "low_data_quality", "data_quality": round(quality, 4)}
    independent = poisson_profile_challenger(profile)
    if independent is None:
        return {**base, "reason": "independent_profile_unavailable"}
    dixon = dixon_coles_profile_challenger(profile)
    p_v5 = score_grid(hl, al)
    p_alt = score_grid(independent.home_lambda, independent.away_lambda)
    candidates: list[dict[str, Any]] = []

    def consider(market: str, selection: str, odd: Any, fair: Any, a: float, b: float, dc: float | None = None):
        price, market_fair = _finite(odd), _finite(fair)
        if price is None or market_fair is None or not (price >= 1.40 and 0 < market_fair < 1):
            return
        if not all(math.isfinite(p) and 0 <= p <= 1 for p in (a, b)):
            return
        # Both independently derived models must agree on which side of
        # bookmaker fair odds to take; don't cherry-pick extreme lines.
        agrees = a > market_fair and b > market_fair
        dispersion = abs(a - b)
        raw_consensus = (a + b) / 2.0
        # Heuristic shrinkage toward market; not fitted calibration.
        market_weight = 0.65
        shrunk = market_weight * market_fair + (1.0 - market_weight) * raw_consensus
        ev = shrunk * price - 1.0
        dc_sensitivity = None if dc is None else round(dc - b, 6)
        if not agrees:
            state, reason = "WAIT", "models_disagree_with_bookmaker"
        elif dispersion > 0.14:
            state, reason = "WAIT", "large_historical_model_disagreement"
        elif dc is not None and abs(dc - b) > 0.07:
            state, reason = "WAIT", "dixon_coles_low_score_sensitivity"
        elif ev < 0.05 or shrunk - market_fair < 0.025:
            state, reason = "WAIT", "not_enough_value_after_conservative_shrinkage"
        else:
            state, reason = "PASS_SHADOW", "consensus_value_not_calibrated"
        candidates.append({
            "market": market,
            "selection": selection,
            "odd": round(price, 4),
            "bookmaker_fair_probability": round(market_fair, 6),
            "v5_probability_uncalibrated": round(a, 6),
            "independent_probability_uncalibrated": round(b, 6),
            "dixon_coles_sensitivity": dc_sensitivity,
            "model_disagreement_pp": round(dispersion * 100.0, 2),
            "consensus_probability_uncalibrated": round(raw_consensus, 6),
            "market_shrunk_probability_heuristic": round(shrunk, 6),
            "heuristic_ev_units": round(ev, 6),
            "state": state,
            "reason": reason,
        })

    totals = quote.get("match_totals") or []
    for row in totals:
        try:
            line = float(row["line"])
            odd_over, odd_under = float(row["over"]), float(row["under"])
        except (KeyError, TypeError, ValueError):
            continue
        if not (math.isfinite(odd_over) and math.isfinite(odd_under) and min(odd_over, odd_under) > 1):
            continue
        pv, pa = _poisson_over(hl + al, line), _poisson_over(independent.total_lambda, line)
        if pv is None or pa is None:
            continue
        fo, fu = devig_two_way(odd_over, odd_under)
        consider("match_total", f"over {line:g}", odd_over, fo, pv, pa)
        consider("match_total", f"under {line:g}", odd_under, fu, 1 - pv, 1 - pa)

    for family, key, va, vb in (
        ("home_total", "home_totals", hl, independent.home_lambda),
        ("away_total", "away_totals", al, independent.away_lambda),
    ):
        for row in quote.get(key) or []:
            try:
                line, o, u = float(row["line"]), float(row["over"]), float(row["under"])
            except (KeyError, TypeError, ValueError):
                continue
            if not (math.isfinite(o) and math.isfinite(u) and min(o, u) > 1):
                continue
            pv, pa = _poisson_over(va, line), _poisson_over(vb, line)
            if pv is None or pa is None:
                continue
            fo, fu = devig_two_way(o, u)
            consider(family, f"over {line:g}", o, fo, pv, pa)
            consider(family, f"under {line:g}", u, fu, 1 - pv, 1 - pa)

    three_way = quote.get("match_1x2") or {}
    try:
        home_odd, draw_odd, away_odd = (
            float(three_way["home"]), float(three_way["draw"]), float(three_way["away"])
        )
        if min(home_odd, draw_odd, away_odd) > 1 and all(
            math.isfinite(v) for v in (home_odd, draw_odd, away_odd)
        ):
            fairs = devig_three_way(home_odd, draw_odd, away_odd)
            for sel, odd, fair in zip(("home", "draw", "away"), (home_odd, draw_odd, away_odd), fairs):
                v5prob = p_v5["draw"] if sel == "draw" else p_v5[f"{sel}_win"]
                altprob = p_alt["draw"] if sel == "draw" else p_alt[f"{sel}_win"]
                dcprob = getattr(dixon, sel) if dixon is not None else None
                consider("match_1x2", sel, odd, fair, v5prob, altprob, dcprob)
    except (KeyError, TypeError, ValueError, ZeroDivisionError):
        pass

    both_score = quote.get("btts") or {}
    try:
        o_yes, o_no = float(both_score["yes"]), float(both_score["no"])
        if min(o_yes, o_no) > 1 and all(math.isfinite(v) for v in (o_yes, o_no)):
            fy, fn = devig_two_way(o_yes, o_no)
            consider("btts", "yes", o_yes, fy, p_v5["btts_yes"], p_alt["btts_yes"])
            consider("btts", "no", o_no, fn, 1 - p_v5["btts_yes"], 1 - p_alt["btts_yes"])
    except (KeyError, TypeError, ValueError, ZeroDivisionError):
        pass

    candidates.sort(key=lambda item: (item["state"] == "PASS_SHADOW", item["heuristic_ev_units"]), reverse=True)
    qualified = sum(r["state"] == "PASS_SHADOW" for r in candidates)
    return {
        **base,
        "status": "PASS_SHADOW" if qualified else "WAIT",
        "reason": "consensus_unvalidated" if qualified else "no_qualified_consensus_at_real_odds",
        "data_quality": round(quality, 4),
        "v5_lambdas": [round(hl, 4), round(al, 4)],
        "independent_lambdas": [round(independent.home_lambda, 4), round(independent.away_lambda, 4)],
        "dixon_coles_used_only_for_sensitivity": dixon is not None,
        "quoted_markets_checked": len(candidates),
        "qualified_shadow_markets": qualified,
        "candidates": candidates[:12],
    }
