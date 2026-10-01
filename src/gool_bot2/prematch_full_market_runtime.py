from __future__ import annotations

import re
from typing import Any

from .v4_prematch_engine import PrematchPick


_FULL_TIME_TYPES = {
    "HOME_DRAW_AWAY",
    "DOUBLE_CHANCE",
    "DRAW_NO_BET",
    "OVER_UNDER",
    "BOTH_TEAMS_TO_SCORE",
    "ASIAN_HANDICAP",
    "EUROPEAN_HANDICAP",
}


def _last_number(value: str) -> float | None:
    found = re.findall(r"[-+]?\d+(?:[.,]\d+)?", str(value or ""))
    if not found:
        return None
    try:
        return float(found[-1].replace(",", "."))
    except ValueError:
        return None


def _supported(row: dict[str, Any], *, min_confidence: float) -> bool:
    if str(row.get("status") or "").upper() != "BET":
        return False
    try:
        if float(row.get("confidence_score") or 0.0) < float(min_confidence):
            return False
        if float(row.get("odds") or 0.0) <= 1.0:
            return False
        if row.get("honest_probability") is None or row.get("market_probability") is None:
            return False
    except (TypeError, ValueError):
        return False

    scope = str(row.get("scope") or "").upper()
    market_type = str(row.get("market_type") or "").upper()
    selection = str(row.get("selection") or "").upper()

    if scope == "FULL_TIME":
        if market_type not in _FULL_TIME_TYPES:
            return False
        if market_type == "ASIAN_HANDICAP":
            # Production journal has won/lost/push states, so quarter handicaps
            # (half-win/half-loss) stay research-only until fractional settlement
            # is represented explicitly.
            line = _last_number(selection)
            if line is None or abs(line * 2 - round(line * 2)) > 1e-8:
                return False
        return True

    if scope in {"FIRST_HALF", "SECOND_HALF"}:
        # Period totals are fully supported by the existing settlement lifecycle.
        # Team-specific period totals are not yet journal-safe.
        return (
            market_type == "OVER_UNDER"
            and not selection.startswith("HOME_")
            and not selection.startswith("AWAY_")
        )

    return False


def _market_and_selection(row: dict[str, Any]) -> tuple[str, str] | None:
    scope = str(row.get("scope") or "").upper()
    typ = str(row.get("market_type") or "").upper()
    selection = str(row.get("selection") or "").strip()
    upper = selection.upper()

    if typ == "OVER_UNDER":
        if scope == "FULL_TIME":
            if upper.startswith("HOME_"):
                return "home_total", selection[5:].replace("_", " ").lower()
            if upper.startswith("AWAY_"):
                return "away_total", selection[5:].replace("_", " ").lower()
            return "match_total", selection.replace("_", " ").lower()
        if scope == "FIRST_HALF":
            return "1H_OVER_UNDER", selection.replace("_", " ").lower()
        if scope == "SECOND_HALF":
            return "2H_OVER_UNDER", selection.replace("_", " ").lower()

    if scope != "FULL_TIME":
        return None

    mapping = {
        "HOME_DRAW_AWAY": "match_1x2",
        "DOUBLE_CHANCE": "double_chance",
        "DRAW_NO_BET": "draw_no_bet",
        "BOTH_TEAMS_TO_SCORE": "btts",
        "ASIAN_HANDICAP": "asian_handicap",
        "EUROPEAN_HANDICAP": "european_handicap",
    }
    market = mapping.get(typ)
    if not market:
        return None
    return market, selection.replace("_", " ").lower()


def select_production_full_market_pick(
    analysis: dict[str, Any],
    *,
    event_id: str,
    home: str,
    away: str,
    league: str,
    kickoff_ts: float,
    min_confidence: float = 70.0,
) -> tuple[PrematchPick, dict[str, Any]] | None:
    candidates = [
        row for row in list(analysis.get("candidates") or [])
        if isinstance(row, dict) and _supported(row, min_confidence=min_confidence)
    ]
    if not candidates:
        return None

    def rank(row: dict[str, Any]) -> tuple[float, float, float, float]:
        return (
            float(row.get("confidence_score") or 0.0),
            float(row.get("probability_range_low") or 0.0),
            float(row.get("honest_probability") or row.get("model_probability") or 0.0),
            float(row.get("expected_value") or 0.0),
        )

    row = max(candidates, key=rank)
    converted = _market_and_selection(row)
    if converted is None:
        return None
    market, selection = converted

    pick = PrematchPick(
        str(event_id),
        str(home),
        str(away),
        market,
        selection,
        float(row.get("odds") or 0.0),
        float(row.get("honest_probability") or row.get("model_probability") or 0.0),
        float(row.get("market_probability") or 0.0),
        float(row.get("quality") or 0.0),
        str(league or ""),
        float(kickoff_ts or 0.0),
    )
    meta = {
        "bookmaker": str(row.get("bookmaker") or ""),
        "full_market_scope": str(row.get("scope") or ""),
        "full_market_type": str(row.get("market_type") or ""),
        "full_market_confidence": float(row.get("confidence_score") or 0.0),
        "full_market_grade": str(row.get("confidence_grade") or ""),
        "full_market_probability_low": row.get("probability_range_low"),
        "full_market_probability_high": row.get("probability_range_high"),
        "full_market_selection": str(row.get("selection") or ""),
    }
    return pick, meta


__all__ = ["select_production_full_market_pick"]
