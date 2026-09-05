from __future__ import annotations

import math
from pathlib import Path
from typing import Any

from .journal import load_signal_journal


def _num(value: Any) -> float | None:
    try:
        if value in (None, "", "-"):
            return None
        number = float(value)
        return number / 100.0 if number > 1.0 else number
    except (TypeError, ValueError):
        return None


def _bucket(probability: float) -> str:
    p = max(0.0, min(1.0, probability))
    low = math.floor(p * 20.0) / 20.0
    high = min(1.0, low + 0.05)
    return f"{int(round(low * 100))}-{int(round(high * 100))}%"


def calibration_summary(rows: list[dict[str, Any]], *, min_sample: int = 1) -> dict[str, Any]:
    """Measure whether GOOL probabilities match observed hit rates.

    This is diagnostic only. It never changes current production thresholds from
    a tiny sample; its job is to reveal over/under-confidence before a later
    calibration update is justified by enough settled bets.
    """
    groups: dict[tuple[str, str], list[tuple[float, int, float | None]]] = {}
    all_used: list[tuple[float, int, float | None]] = []
    for row in rows:
        result = str(row.get("result") or "").lower()
        if result not in {"won", "lost"}:
            continue
        calibration = row.get("calibration") or {}
        predicted = _num(calibration.get("predicted_probability"))
        if predicted is None:
            predicted = _num(row.get("probability"))
        if predicted is None:
            continue
        fair = _num(calibration.get("market_fair_probability"))
        if fair is None:
            fair = _num(row.get("market_probability"))
        outcome = 1 if result == "won" else 0
        strategy = str(row.get("strategy") or calibration.get("strategy") or "unknown")
        bucket = str(calibration.get("probability_bucket") or _bucket(predicted))
        item = (predicted, outcome, fair)
        groups.setdefault((strategy, bucket), []).append(item)
        all_used.append(item)

    def summarize(items: list[tuple[float, int, float | None]]) -> dict[str, Any]:
        count = len(items)
        avg_p = sum(item[0] for item in items) / count
        actual = sum(item[1] for item in items) / count
        brier = sum((item[0] - item[1]) ** 2 for item in items) / count
        fair_values = [item[2] for item in items if item[2] is not None]
        fair_avg = None if not fair_values else sum(fair_values) / len(fair_values)
        return {
            "sample": count,
            "predicted": round(avg_p, 4),
            "actual": round(actual, 4),
            "calibration_error_pp": round((actual - avg_p) * 100.0, 2),
            "brier": round(brier, 5),
            "market_fair": None if fair_avg is None else round(fair_avg, 4),
            "model_vs_market_pp": None if fair_avg is None else round((avg_p - fair_avg) * 100.0, 2),
        }

    buckets = []
    for (strategy, bucket), items in sorted(groups.items()):
        if len(items) < max(1, int(min_sample)):
            continue
        buckets.append({"strategy": strategy, "bucket": bucket, **summarize(items)})
    return {
        "settled_sample": len(all_used),
        "overall": None if not all_used else summarize(all_used),
        "buckets": buckets,
        "min_sample": max(1, int(min_sample)),
        "production_effect": "diagnostic_only",
    }


def calibration_from_journal(path: Path, *, min_sample: int = 5) -> dict[str, Any]:
    return calibration_summary(load_signal_journal(path), min_sample=min_sample)


__all__ = ["calibration_from_journal", "calibration_summary"]
