from __future__ import annotations

from typing import Any, Mapping


def _count(value: Any) -> int:
    try:
        return max(0, int(value or 0))
    except (TypeError, ValueError):
        return 0


def _clamp(value: float) -> float:
    return max(0.0, min(1.0, float(value)))


def prematch_evidence_quality(
    profile: Mapping[str, Any],
    *,
    source_coverage: Mapping[str, Any] | None = None,
) -> float:
    """Evidence quality for PREMATCH football profiles.

    Quality is intentionally not a synonym for sample size. It combines:
    - usable history depth,
    - period structure (1H/2H/full time),
    - home/away venue splits,
    - H2H support,
    - independent provider coverage.

    The 0.35 floor prevents sparse-but-valid profiles from becoming numerically
    meaningless while keeping 1.00 reserved for genuinely broad evidence.
    """
    first = dict(profile.get("first_half") or {})
    second = dict(profile.get("second_half") or {})
    full = dict(profile.get("full_match") or {})

    sample = max(
        _count(first.get("pair_sample")),
        _count(second.get("pair_sample")),
        _count(full.get("pair_sample")),
    )
    sample_score = _clamp(sample / 16.0)

    period_score = sum(
        1 for period in (first, second, full) if bool(period.get("available"))
    ) / 3.0

    home_venue = max(
        _count((first.get("home") or {}).get("venue_matches")),
        _count((second.get("home") or {}).get("venue_matches")),
    )
    away_venue = max(
        _count((first.get("away") or {}).get("venue_matches")),
        _count((second.get("away") or {}).get("venue_matches")),
    )
    venue_score = _clamp(min(home_venue, away_venue) / 5.0)

    h2h = max(
        _count((first.get("h2h") or {}).get("matches")),
        _count((second.get("h2h") or {}).get("matches")),
    )
    h2h_score = _clamp(h2h / 5.0)

    coverage = dict(source_coverage or {})
    source_count = sum(1 for value in coverage.values() if _count(value) > 0)
    if source_count == 0:
        source_count = len({str(x) for x in (profile.get("sources") or []) if str(x).strip()})
    source_score = _clamp(source_count / 3.0)

    quality = (
        0.35
        + 0.30 * sample_score
        + 0.15 * period_score
        + 0.08 * venue_score
        + 0.05 * h2h_score
        + 0.07 * source_score
    )
    return round(_clamp(quality), 4)


__all__ = ["prematch_evidence_quality"]
