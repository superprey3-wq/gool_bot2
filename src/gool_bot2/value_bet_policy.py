from __future__ import annotations

import os
from typing import Any

VALUE_LEVELS = ("NO_VALUE", "VALUE", "STRONG_VALUE", "VERY_STRONG_VALUE")
ABSOLUTE_MIN_BET_ODD = 1.40


def _prob(value: Any) -> float | None:
    try:
        p = float(value)
    except (TypeError, ValueError):
        return None
    if p > 1.0:
        p /= 100.0
    return p if 0.0 <= p <= 1.0 else None


def _value_target(info: dict[str, Any]) -> dict[str, Any]:
    targets = [row for row in (info.get("targets") or []) if isinstance(row, dict)]
    if not targets:
        return {}
    head = str(info.get("head") or "")
    if head == "both_teams_to_score":
        for row in targets:
            if str(row.get("market") or "") == "btts_yes":
                return row
    targets.sort(key=lambda row: float(row.get("weight") or 0.0), reverse=True)
    return targets[0]


def _fair_probability(odd: Any, opposite: Any) -> float | None:
    """Normalize a two-way price when a caller did not precompute fair P.

    Most xBet selections already carry ``prob`` from the market decoder, but
    exact first-half totals are built later from the 1st-half subgame. Keeping
    this fallback in the VALUE policy makes those markets (and future two-way
    markets) impossible to silently lose VALUE/override just because ``prob``
    was omitted by an adapter.
    """
    try:
        primary = float(odd)
    except (TypeError, ValueError):
        return None
    if primary <= 1.0:
        return None
    a = 1.0 / primary
    try:
        other = float(opposite)
    except (TypeError, ValueError):
        other = 0.0
    if other <= 1.0:
        return a
    b = 1.0 / other
    return a / (a + b) if a + b > 0 else None


def attach_value(info: dict[str, Any] | None, model_probability: Any, *, probability_source: str = "gool") -> dict[str, Any]:
    out = dict(info or {})
    model_p = _prob(model_probability)
    target = _value_target(out)
    selection = target.get("selection") if isinstance(target, dict) else None
    selection = selection if isinstance(selection, dict) else {}
    market_p = _prob(selection.get("prob"))
    try:
        odd = float(selection.get("odd")) if selection.get("odd") is not None else None
    except (TypeError, ValueError):
        odd = None
    if market_p is None and odd is not None:
        market_p = _fair_probability(odd, selection.get("opposite"))

    out.update({
        "value_model_probability": model_p,
        "value_market_probability": market_p,
        "value_odd": odd,
        "value_probability_source": probability_source,
        "value_market_label": target.get("label") if isinstance(target, dict) else None,
    })

    if model_p is None or market_p is None or odd is None:
        out.update({"value_edge_pp": None, "value_level": "NO_DATA", "value_bet": False, "value_override": False})
        return out

    edge_pp = (model_p - market_p) * 100.0
    min_edge = float(os.getenv("XBET_VALUE_MIN_EDGE_PP", "5"))
    strong_edge = float(os.getenv("XBET_VALUE_STRONG_EDGE_PP", "8"))
    very_edge = float(os.getenv("XBET_VALUE_VERY_STRONG_EDGE_PP", "12"))
    min_model = float(os.getenv("XBET_VALUE_MIN_MODEL_PROBABILITY", "0.60"))
    override_model = float(os.getenv("XBET_VALUE_OVERRIDE_MIN_MODEL_PROBABILITY", "0.65"))
    min_odd = max(ABSOLUTE_MIN_BET_ODD, float(os.getenv("XBET_VALUE_MIN_ODD", str(ABSOLUTE_MIN_BET_ODD))))
    max_odd = float(os.getenv("XBET_VALUE_MAX_ODD", "4.50"))
    max_age = float(os.getenv("XBET_VALUE_MAX_AGE_SECONDS", "35"))
    try:
        age = float(out.get("age_seconds"))
    except (TypeError, ValueError):
        age = 999999.0
    fresh = age <= max_age
    price_ok = min_odd <= odd <= max_odd

    if edge_pp >= very_edge:
        level = "VERY_STRONG_VALUE"
    elif edge_pp >= strong_edge:
        level = "STRONG_VALUE"
    elif edge_pp >= min_edge:
        level = "VALUE"
    else:
        level = "NO_VALUE"

    value_bet = bool(fresh and price_ok and model_p >= min_model and edge_pp >= min_edge)
    value_override = bool(fresh and price_ok and model_p >= override_model and edge_pp >= strong_edge)
    out.update({
        "value_edge_pp": round(edge_pp, 2),
        "value_level": level,
        "value_bet": value_bet,
        "value_override": value_override,
        "value_fresh": fresh,
        "value_price_ok": price_ok,
        "value_min_edge_pp": min_edge,
        "value_strong_edge_pp": strong_edge,
        "value_min_odd": min_odd,
        "value_max_odd": max_odd,
    })
    if value_override:
        out["value_reason"] = f"VALUE OVERRIDE · {level} · GOOL {model_p*100:.1f}% vs 1xBet {market_p*100:.1f}% · edge +{edge_pp:.1f} п.п. · кэф {odd:.2f}"
    elif value_bet:
        out["value_reason"] = f"VALUE BET · {level} · edge +{edge_pp:.1f} п.п. · кэф {odd:.2f}"
    else:
        out["value_reason"] = f"VALUE {level} · edge {edge_pp:+.1f} п.п."
    return out


def is_value_row(row: dict[str, Any]) -> bool:
    info = row.get("xbet_market") or ((row.get("analysis") or {}).get("xbet_market") or {})
    return bool(
        str(row.get("signal_source") or "") in {"xbet_value_bet", "xbet_value_override"}
        or row.get("value_bet")
        or row.get("value_override")
        or (isinstance(info, dict) and info.get("value_bet"))
    )
