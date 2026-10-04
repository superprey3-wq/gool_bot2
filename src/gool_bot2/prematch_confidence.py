from __future__ import annotations

import os
from typing import Any, Iterable


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


def _confidence_tuple(row: dict[str, Any]) -> tuple[float, ...]:
    primary = row.get("primary_trend") or {}
    rank_score = float(primary.get("rank_score") or 0.0)
    separation = float(primary.get("separation") or 0.0)
    agreement = float(primary.get("agreement") or 0.0)
    probability = float(primary.get("probability") or 0.0)
    sample = float(primary.get("sample") or row.get("sample") or 0.0)
    brain = float(row.get("brain_score") or 0.0)
    quality = float(row.get("quality") or 0.0)
    # Rank score already contains calibrated probability, strength, sample and
    # agreement. The remaining terms make ties deterministic and favor profiles
    # that are clearly separated from their runner-up tendency.
    return (rank_score, separation, agreement, probability, sample, brain, quality)


def select_confident_prematch_rows(
    rows: Iterable[dict[str, Any]],
    *,
    max_rows: int | None = None,
) -> tuple[list[dict[str, Any]], dict[str, int | float]]:
    """Return the strong PREMATCH shortlist plus a bounded evidence-quality rescue.

    The normal gate remains unchanged. A fixture rejected *only* because evidence
    quality is a little below the main threshold can be rescued when every
    structural signal is stronger than normal. This prevents provider/H2H
    coverage from reducing a day with hundreds of matches to one card, without
    turning sparse or ambiguous profiles into public bets.
    """
    rows = [row for row in rows if isinstance(row, dict) and row.get("primary_trend")]
    max_rows = max(1, int(max_rows if max_rows is not None else _i("GOOL_PREMATCH_SHORTLIST_MAX", 120)))
    min_sample = max(1, _i("GOOL_PREMATCH_SHORTLIST_MIN_SAMPLE", 8))
    min_agreement = max(0.0, min(1.0, _f("GOOL_PREMATCH_SHORTLIST_MIN_AGREEMENT", 0.70)))
    min_separation = max(0.0, _f("GOOL_PREMATCH_SHORTLIST_MIN_SEPARATION", 0.020))
    min_probability = max(0.0, min(1.0, _f("GOOL_PREMATCH_SHORTLIST_MIN_PROBABILITY", 0.64)))
    min_quality = max(0.0, min(1.0, _f("GOOL_PREMATCH_SHORTLIST_MIN_QUALITY", 0.62)))

    rescue_min_quality = max(0.0, min(min_quality, _f("GOOL_PREMATCH_RESCUE_MIN_QUALITY", 0.55)))
    rescue_min_sample = max(min_sample, _i("GOOL_PREMATCH_RESCUE_MIN_SAMPLE", 10))
    rescue_min_agreement = max(min_agreement, min(1.0, _f("GOOL_PREMATCH_RESCUE_MIN_AGREEMENT", 0.76)))
    rescue_min_separation = max(min_separation, _f("GOOL_PREMATCH_RESCUE_MIN_SEPARATION", 0.030))
    rescue_min_probability = max(min_probability, min(1.0, _f("GOOL_PREMATCH_RESCUE_MIN_PROBABILITY", 0.68)))
    rescue_max = max(0, _i("GOOL_PREMATCH_RESCUE_MAX", 12))

    kept: list[dict[str, Any]] = []
    quality_rescue_pool: list[dict[str, Any]] = []
    reject_sample = reject_agreement = reject_separation = reject_probability = reject_quality = 0

    for row in rows:
        primary = row.get("primary_trend") or {}
        sample = int(primary.get("sample") or row.get("sample") or 0)
        agreement = float(primary.get("agreement") or 0.0)
        separation = float(primary.get("separation") or 0.0)
        probability = float(primary.get("probability") or 0.0)
        quality = float(row.get("quality") or 0.0)

        if sample < min_sample:
            reject_sample += 1
            continue
        if agreement < min_agreement:
            reject_agreement += 1
            continue
        if separation < min_separation:
            reject_separation += 1
            continue
        if probability < min_probability:
            reject_probability += 1
            continue
        if quality < min_quality:
            reject_quality += 1
            if (
                rescue_max > 0
                and quality >= rescue_min_quality
                and sample >= rescue_min_sample
                and agreement >= rescue_min_agreement
                and separation >= rescue_min_separation
                and probability >= rescue_min_probability
            ):
                quality_rescue_pool.append({**row, "confidence_tier": "QUALITY_RESCUE"})
            continue
        kept.append({**row, "confidence_tier": str(row.get("confidence_tier") or "PRIMARY")})

    kept.sort(key=_confidence_tuple, reverse=True)
    quality_rescue_pool.sort(key=_confidence_tuple, reverse=True)
    rescued = quality_rescue_pool[:rescue_max]

    # Primary evidence always wins ranking priority. Rescue candidates fill only
    # remaining shortlist capacity and remain clearly tagged for diagnostics.
    selected = kept[:max_rows]
    remaining = max(0, max_rows - len(selected))
    selected.extend(rescued[:remaining])

    stats: dict[str, int | float] = {
        "input": len(rows),
        "qualified": len(kept) + len(rescued),
        "qualified_primary": len(kept),
        "selected": len(selected),
        "cap": max_rows,
        "rescued_quality": min(len(rescued), remaining),
        "rescue_pool": len(quality_rescue_pool),
        "rescue_max": rescue_max,
        "rejected_sample": reject_sample,
        "rejected_agreement": reject_agreement,
        "rejected_separation": reject_separation,
        "rejected_probability": reject_probability,
        "rejected_quality": reject_quality,
        "min_sample": min_sample,
        "min_agreement": min_agreement,
        "min_separation": min_separation,
        "min_probability": min_probability,
        "min_quality": min_quality,
        "rescue_min_quality": rescue_min_quality,
        "rescue_min_sample": rescue_min_sample,
        "rescue_min_agreement": rescue_min_agreement,
        "rescue_min_separation": rescue_min_separation,
        "rescue_min_probability": rescue_min_probability,
    }
    return selected, stats


__all__ = ["select_confident_prematch_rows"]
