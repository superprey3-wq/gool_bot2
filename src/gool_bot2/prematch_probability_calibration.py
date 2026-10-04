from __future__ import annotations

import math
import os
from pathlib import Path
from typing import Any

from .journal import load_signal_journal


def _clamp(value: float, low: float = 0.01, high: float = 0.99) -> float:
    return max(low, min(high, float(value)))


def _num(value: Any) -> float | None:
    try:
        if value in (None, "", "-"):
            return None
        x = float(value)
        if x > 1.0:
            x /= 100.0
        return _clamp(x, 0.0, 1.0)
    except (TypeError, ValueError):
        return None


def _profile_sample(profile: dict[str, Any], scope: str) -> int:
    key = {
        "FULL_TIME": "full_match",
        "FIRST_HALF": "first_half",
        "SECOND_HALF": "second_half",
    }.get(str(scope).upper())
    if not key:
        return 0
    try:
        return max(0, int((profile.get(key) or {}).get("pair_sample") or 0))
    except (TypeError, ValueError):
        return 0


def _bucket(probability: float) -> tuple[float, float]:
    p = _clamp(probability, 0.0, 1.0)
    low = math.floor(p * 20.0) / 20.0
    return low, min(1.0, low + 0.05)


def _row_identity(row: dict[str, Any]) -> tuple[str, str]:
    scope = str(row.get("scope") or "").upper().strip()
    typ = str(row.get("market_type") or "").upper().strip()
    if scope and typ:
        return scope, typ

    family = str(row.get("market_family") or "").casefold()
    market = str(row.get("market") or "").casefold()
    selection = str(row.get("selection") or "").casefold()
    text = f"{market} {selection}"

    if family == "first_half_total" or "1h_" in market or "1-й тайм" in text:
        return "FIRST_HALF", "OVER_UNDER"
    if family == "second_half_total" or "2h_" in market or "2-й тайм" in text:
        return "SECOND_HALF", "OVER_UNDER"
    if family in {"btts"} or "btts" in text or "обе забьют" in text:
        return "FULL_TIME", "BOTH_TEAMS_TO_SCORE"
    if family in {"match_1x2", "1x2"} or market in {"match_1x2", "1x2"}:
        return "FULL_TIME", "HOME_DRAW_AWAY"
    if family in {"home_total", "away_total", "team_total"} or market in {"home_total", "away_total"}:
        return "FULL_TIME", "TEAM_TOTAL"
    if family == "match_total" or "over" in text or "under" in text or "тб" in text or "тм" in text:
        return "FULL_TIME", "OVER_UNDER"
    return "", ""


def _candidate_type(market_type: str, selection: str) -> str:
    typ = str(market_type or "").upper()
    sel = str(selection or "").upper()
    if typ == "OVER_UNDER" and (sel.startswith("HOME_") or sel.startswith("AWAY_")):
        return "TEAM_TOTAL"
    return typ


def _historical_cell(
    rows: list[dict[str, Any]],
    *,
    scope: str,
    market_type: str,
    predicted: float,
) -> dict[str, Any]:
    target_scope = str(scope).upper()
    target_type = str(market_type).upper()
    low, high = _bucket(predicted)

    exact: list[tuple[float, int]] = []
    family: list[tuple[float, int]] = []
    for row in rows:
        if str(row.get("origin") or "").casefold() != "prematch":
            continue
        result = str(row.get("result") or "").casefold()
        if result not in {"won", "lost"}:
            continue
        row_scope, row_type = _row_identity(row)
        if not row_scope or not row_type:
            continue
        p = _num(row.get("honest_probability"))
        if p is None:
            p = _num(row.get("probability"))
        if p is None:
            p = _num(row.get("model_probability"))
        if p is None:
            continue
        item = (p, 1 if result == "won" else 0)
        if row_scope == target_scope and row_type == target_type:
            family.append(item)
            if low <= p < high or (high >= 1.0 and p <= high):
                exact.append(item)

    chosen = exact if len(exact) >= 8 else family
    source = "scope_market_bucket" if chosen is exact and exact else "scope_market"
    if not chosen:
        return {"sample": 0, "wins": 0, "hit_rate": None, "avg_predicted": None, "source": "none"}
    return {
        "sample": len(chosen),
        "wins": sum(x[1] for x in chosen),
        "hit_rate": sum(x[1] for x in chosen) / len(chosen),
        "avg_predicted": sum(x[0] for x in chosen) / len(chosen),
        "source": source,
    }


def load_prematch_calibration_rows(path: Path | None = None) -> list[dict[str, Any]]:
    if path is None:
        raw = os.getenv("GOOL_MULTI_JOURNAL_PATH", "").strip()
        if raw:
            path = Path(raw)
        else:
            path = Path(os.getenv("RUNTIME_DATA_DIR", "data")) / "live" / "gool_multi_journal.json"
    try:
        return list(load_signal_journal(path)) if path.exists() else []
    except Exception:
        return []


def calibrate_probability(
    *,
    raw_model_probability: float,
    market_probability: float | None,
    profile: dict[str, Any],
    scope: str,
    market_type: str,
    selection: str = "",
    quality: float = 1.0,
    rows: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Return a conservative, explainable probability for user-facing cards.

    The raw model is never presented directly as a hit rate. First it is shrunk
    toward the de-vig market prior according to historical/profile support. Then,
    when settled prematch journal rows exist for the same scope+market family,
    observed hit rate pulls the estimate further through an empirical-Bayes step.
    """
    raw = _clamp(raw_model_probability)
    market = None if market_probability is None else _clamp(market_probability)
    q = _clamp(quality, 0.0, 1.0)
    sample = _profile_sample(profile, scope)

    # With no historical calibration, the market remains the strongest prior.
    # Model influence rises with both pair sample and source/data quality but is
    # deliberately capped below 50% until settlement history proves reliability.
    support = sample / (sample + 10.0) if sample > 0 else 0.0
    model_weight = 0.16 + 0.30 * support * q
    model_weight = max(0.12, min(0.46, model_weight))

    if market is not None:
        prior = market + model_weight * (raw - market)
        prior_source = "model_market_shrink"
    else:
        reliability = 0.30 + 0.40 * support * q
        reliability = max(0.25, min(0.70, reliability))
        prior = 0.50 + reliability * (raw - 0.50)
        prior_source = "model_neutral_shrink"

    normalized_type = _candidate_type(market_type, selection)
    history = _historical_cell(
        list(rows if rows is not None else load_prematch_calibration_rows()),
        scope=scope,
        market_type=normalized_type,
        predicted=prior,
    )

    n = int(history["sample"])
    wins = int(history["wins"])
    # Bucket calibration may map toward its observed hit rate because those rows
    # have similar predicted probabilities. A broad market-family fallback must
    # NOT do that: its absolute hit rate is selection-biased (ordinary published
    # bets are usually high-probability) and can wildly inflate a new longshot.
    # For the family fallback, learn only historical residual bias:
    # observed hit rate - historical average prediction.
    prior_strength = 24.0
    if n > 0 and str(history.get("source") or "") == "scope_market_bucket":
        honest = (wins + prior_strength * prior) / (n + prior_strength)
        calibration_mode = "bucket_absolute"
    elif n > 0:
        hit_rate = float(history.get("hit_rate") or 0.0)
        avg_predicted = float(history.get("avg_predicted") or 0.0)
        residual = hit_rate - avg_predicted
        evidence_weight = n / (n + prior_strength)
        honest = prior + evidence_weight * residual
        calibration_mode = "family_residual"
    else:
        honest = prior
        calibration_mode = "prior_only"
    honest = _clamp(honest)

    # Reliability band: not a promise/CI, but a compact uncertainty indicator.
    # The effective count includes profile support only weakly; settled bets are
    # what make the band narrow.
    effective_n = prior_strength + n + min(sample, 20) * 0.35
    se = math.sqrt(max(1e-9, honest * (1.0 - honest) / effective_n))
    margin = min(0.18, max(0.035, 1.28 * se))
    low = _clamp(honest - margin, 0.01, 0.99)
    high = _clamp(honest + margin, 0.01, 0.99)

    if n >= 80:
        confidence = "HIGH"
    elif n >= 30:
        confidence = "MEDIUM"
    else:
        confidence = "LOW"

    return {
        "raw_model_probability": round(raw, 6),
        "market_probability": None if market is None else round(market, 6),
        "honest_probability": round(honest, 6),
        "range_low": round(low, 6),
        "range_high": round(high, 6),
        "profile_sample": sample,
        "calibration_sample": n,
        "calibration_wins": wins,
        "historical_hit_rate": None if history["hit_rate"] is None else round(float(history["hit_rate"]), 6),
        "historical_avg_predicted": None if history["avg_predicted"] is None else round(float(history["avg_predicted"]), 6),
        "confidence": confidence,
        "source": f"{prior_source}+{history['source']}" if n else prior_source,
        "calibration_mode": calibration_mode,
        "prior_probability": round(prior, 6),
        "model_weight": round(model_weight, 4),
        "market_edge": None if market is None else round(honest - market, 6),
    }


__all__ = ["calibrate_probability", "load_prematch_calibration_rows"]
