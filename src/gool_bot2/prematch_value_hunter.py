from __future__ import annotations

import os
from typing import Any, Iterable

from .prematch_full_market_runtime import _market_and_selection
from .v4_prematch_engine import PrematchPick


_ALLOWED_FULL_TIME_TYPES = {
    "HOME_DRAW_AWAY",
    "DOUBLE_CHANCE",
    "DRAW_NO_BET",
    "OVER_UNDER",
    "BOTH_TEAMS_TO_SCORE",
    "ASIAN_HANDICAP",
    "EUROPEAN_HANDICAP",
}


def _f(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        return float(default)


def _i(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        return int(default)


def _dynamic_gates(odds: float) -> tuple[float, float]:
    """Higher prices need stronger evidence, not merely a higher payout."""
    if odds >= 4.0:
        return max(.10, _f("GOOL_VALUE_HUNTER_EDGE_4PLUS", .10)), max(.25, _f("GOOL_VALUE_HUNTER_EV_4PLUS", .25))
    if odds >= 3.0:
        return max(.09, _f("GOOL_VALUE_HUNTER_EDGE_3PLUS", .09)), max(.18, _f("GOOL_VALUE_HUNTER_EV_3PLUS", .18))
    return max(.08, _f("GOOL_VALUE_HUNTER_EDGE", .08)), max(.15, _f("GOOL_VALUE_HUNTER_EV", .15))


def analysis_value_rows(
    analysis: dict[str, Any],
    *,
    event_id: str,
    home: str,
    away: str,
    league: str,
    kickoff_ts: float,
) -> list[tuple[PrematchPick, dict[str, Any]]]:
    """Convert journal-safe FULL_TIME markets into VALUE-HUNTER candidates.

    This stage intentionally does not trust FullMarketCandidate.status because
    the normal PREMATCH policy caps ordinary prices near 3.25. Hunter has its
    own stricter high-odds gates applied after TEAM REGIME correction.
    """
    out: list[tuple[PrematchPick, dict[str, Any]]] = []
    seen: set[tuple[str, str]] = set()
    for row in analysis.get("candidates") or []:
        if not isinstance(row, dict):
            continue
        if str(row.get("scope") or "").upper() != "FULL_TIME":
            continue
        if str(row.get("market_type") or "").upper() not in _ALLOWED_FULL_TIME_TYPES:
            continue
        converted = _market_and_selection(row)
        if converted is None:
            continue
        market, selection = converted
        identity = (market, selection)
        if identity in seen:
            continue
        seen.add(identity)
        try:
            p = float(row.get("honest_probability") or row.get("model_probability"))
            mp = float(row.get("market_probability"))
            odds = float(row.get("odds"))
            quality = float(row.get("quality") or 0.0)
        except (TypeError, ValueError):
            continue
        pick = PrematchPick(
            str(event_id), str(home), str(away), market, selection, odds,
            p, mp, quality, str(league or ""), float(kickoff_ts or 0.0),
        )
        out.append((pick, {
            "bookmaker": str(row.get("bookmaker") or ""),
            "full_market_scope": str(row.get("scope") or ""),
            "full_market_type": str(row.get("market_type") or ""),
            "probability_low": row.get("probability_range_low"),
            "probability_high": row.get("probability_range_high"),
            "profile_sample": int(row.get("profile_sample") or 0),
            "calibration_sample": int(row.get("calibration_sample") or 0),
            "calibration_confidence": str(row.get("calibration_confidence") or ""),
            "observations": int(row.get("observations") or 0),
            "raw_model_probability": row.get("raw_model_probability"),
        }))
    return out


def qualify_value_pick(pick: PrematchPick, meta: dict[str, Any]) -> tuple[bool, dict[str, Any]]:
    odds = float(pick.odds)
    min_odds = _f("GOOL_VALUE_HUNTER_MIN_ODDS", 2.20)
    max_odds = _f("GOOL_VALUE_HUNTER_MAX_ODDS", 6.00)
    min_quality = _f("GOOL_VALUE_HUNTER_MIN_QUALITY", .82)
    min_probability = _f("GOOL_VALUE_HUNTER_MIN_PROBABILITY", .24)
    min_profile_sample = _i("GOOL_VALUE_HUNTER_MIN_PROFILE_SAMPLE", 8)
    edge_gate, ev_gate = _dynamic_gates(odds)

    low = meta.get("probability_low")
    try:
        low = None if low is None else float(low)
    except (TypeError, ValueError):
        low = None
    original_p = meta.get("probability_before_regime")
    try:
        original_p = None if original_p is None else float(original_p)
    except (TypeError, ValueError):
        original_p = None
    if low is not None and original_p is not None:
        low = max(0.0, min(1.0, low + float(pick.model_probability) - original_p))

    robust_edge = None if low is None else low - float(pick.market_probability)
    reasons: list[str] = []
    if not (min_odds <= odds <= max_odds): reasons.append("odds")
    if pick.data_quality < min_quality: reasons.append("quality")
    if pick.model_probability < min_probability: reasons.append("probability")
    if int(meta.get("profile_sample") or 0) < min_profile_sample: reasons.append("profile_sample")
    if pick.edge < edge_gate: reasons.append("edge")
    if pick.expected_value < ev_gate: reasons.append("ev")
    if low is None: reasons.append("probability_low_missing")
    elif robust_edge is not None and robust_edge < .01: reasons.append("lower_bound_not_above_market")

    # Asian quarter-lines are not public until fractional settlement is represented.
    if pick.market == "asian_handicap":
        import re
        nums = re.findall(r"[-+]?\d+(?:[.,]\d+)?", str(pick.selection))
        try:
            line = float(nums[-1].replace(",", ".")) if nums else None
        except ValueError:
            line = None
        if line is None or abs(line * 2 - round(line * 2)) > 1e-8:
            reasons.append("asian_quarter_line")

    value_score = (
        100.0 * min(.18, max(0.0, pick.edge)) / .18 * .36
        + 100.0 * min(.40, max(0.0, pick.expected_value)) / .40 * .30
        + 100.0 * max(0.0, min(1.0, pick.data_quality)) * .20
        + 100.0 * min(.65, max(0.0, pick.model_probability)) / .65 * .14
    )
    return not reasons, {
        "value_score": round(value_score, 2),
        "edge_gate": edge_gate,
        "ev_gate": ev_gate,
        "adjusted_probability_low": low,
        "robust_edge": robust_edge,
        "reasons": reasons,
    }


def select_best_value_pick(
    rows: Iterable[tuple[PrematchPick, dict[str, Any]]],
) -> tuple[PrematchPick, dict[str, Any]] | None:
    qualified: list[tuple[PrematchPick, dict[str, Any]]] = []
    for pick, meta in rows:
        ok, gate = qualify_value_pick(pick, meta)
        if not ok:
            continue
        qualified.append((pick, {**meta, **gate}))
    if not qualified:
        return None
    qualified.sort(
        key=lambda item: (
            float(item[1].get("value_score") or 0.0),
            item[0].edge,
            item[0].expected_value,
            item[0].data_quality,
        ),
        reverse=True,
    )
    return qualified[0]


def diagnose_value_rows(
    rows: Iterable[tuple[PrematchPick, dict[str, Any]]],
) -> dict[str, Any]:
    items = list(rows)
    rejects: dict[str, int] = {}
    high_odds = 0
    qualified = 0
    for pick, meta in items:
        if _f("GOOL_VALUE_HUNTER_MIN_ODDS", 2.20) <= float(pick.odds) <= _f("GOOL_VALUE_HUNTER_MAX_ODDS", 6.00):
            high_odds += 1
        ok, gate = qualify_value_pick(pick, meta)
        if ok:
            qualified += 1
        else:
            for reason in gate.get("reasons") or []:
                rejects[str(reason)] = rejects.get(str(reason), 0) + 1
    return {
        "modeled_markets": len(items),
        "high_odds_markets": high_odds,
        "qualified": qualified,
        "rejects": rejects,
    }


__all__ = ["analysis_value_rows", "qualify_value_pick", "select_best_value_pick", "diagnose_value_rows"]
