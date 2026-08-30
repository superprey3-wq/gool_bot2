from __future__ import annotations

import pickle
from pathlib import Path
from typing import Any

import pandas as pd

from .archive_dataset import BASE_FEATURE_COLUMNS
from .match_context import live_rich_features


def live_foundation_features(record: dict[str, Any]) -> pd.DataFrame:
    """Build the exact archive/open-event feature vector from one live record."""
    match = record.get("match", {})
    minute = float(match.get("minute") or 0.0)
    home_score = int(match.get("home_score") or 0)
    away_score = int(match.get("away_score") or 0)
    is_halftime = bool(match.get("is_halftime"))
    period = 1 if is_halftime or minute <= 45 else 2

    providers = record.get("providers", {})
    fs_meta = (providers.get("flashscore") or {}).get("meta") or {}
    goals = fs_meta.get("goal_timeline") or []
    seen_minutes: list[float] = []
    for goal in goals:
        try:
            value = float(goal.get("minute") or 0.0)
        except (TypeError, ValueError, AttributeError):
            continue
        if value <= minute:
            seen_minutes.append(value)
    last_goal = max(seen_minutes, default=None)

    row: dict[str, Any] = {
        "minute": minute,
        "period": period,
        "home_score": home_score,
        "away_score": away_score,
        "total_goals": home_score + away_score,
        "score_diff": home_score - away_score,
        "time_remaining_nominal": max(0.0, 90.0 - minute),
        "goals_last_5m": float(sum(1 for value in seen_minutes if value > minute - 5)),
        "goals_last_10m": float(sum(1 for value in seen_minutes if value > minute - 10)),
        "minutes_since_last_goal": float(minute - last_goal) if last_goal is not None else minute,
    }
    row.update(live_rich_features(record))
    return pd.DataFrame([row]).reindex(columns=BASE_FEATURE_COLUMNS)


class ArchiveFoundationPredictor:
    """Small local predictor: no external AI/API and no request limits."""

    def __init__(self, model_path: str | Path = "models/archive_foundation.pkl") -> None:
        self.model_path = Path(model_path)
        with self.model_path.open("rb") as handle:
            bundle = pickle.load(handle)
        self.bundle = bundle
        self.heads = bundle["heads"]

    def predict(self, record: dict[str, Any]) -> dict[str, float | None]:
        frame = live_foundation_features(record)
        minute = float(record.get("match", {}).get("minute") or 0.0)
        is_halftime = bool(record.get("match", {}).get("is_halftime"))
        output: dict[str, float | None] = {}
        for name, head in self.heads.items():
            if name == "goal_before_ht" and (is_halftime or minute > 45):
                output[name] = None
                continue
            if name == "two_plus_goals_second_half" and minute > 45 and not is_halftime:
                output[name] = None
                continue
            output[name] = float(head.predict_proba(frame)[0])
        return output
