from __future__ import annotations

import os
import pickle
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from .archive_football_data import PRIOR_COLUMNS
from .foundation_inference import ArchiveFoundationPredictor, live_foundation_features
from .train_hazard_models import hazard_probabilities


LIVE_HEADS = ("another_goal", "goal_before_ht", "over_2_5", "both_teams_to_score")


def _football_data_halftime_frame(record: dict[str, Any], feature_columns: list[str]) -> pd.DataFrame:
    """Build the halftime feature row expected by the trained Football-Data heads.

    The historical rolling priors are optional at live time. Missing priors stay
    NaN, which HistGradientBoosting handles natively, while the known halftime
    score is always populated.
    """
    match = record.get("match") or {}
    home_score = int(match.get("home_score") or 0)
    away_score = int(match.get("away_score") or 0)
    row: dict[str, float] = {column: float("nan") for column in feature_columns}
    row.update(
        {
            "home_score": float(home_score),
            "away_score": float(away_score),
            "total_goals": float(home_score + away_score),
            "score_diff": float(home_score - away_score),
        }
    )

    # If a provider/runtime enrichment supplies the same rolling priors, use it.
    priors = record.get("football_data_priors") or {}
    if isinstance(priors, dict):
        for column in PRIOR_COLUMNS:
            value = priors.get(column)
            if value is None:
                continue
            try:
                row[column] = float(value)
            except (TypeError, ValueError):
                pass
    return pd.DataFrame([row]).reindex(columns=feature_columns)


class LocalFootballEnsemble:
    """Local, API-free GOOL model stack for the Monkey server.

    `another_goal` and `goal_before_ht` use the event direct + hazard stack.
    The already-trained Football-Data halftime models provide `over_2_5` and
    `both_teams_to_score` cards at halftime. The latter are single-model heads,
    so their disagreement is reported as zero rather than pretending that a
    second expert exists.
    """

    def __init__(
        self,
        direct_model_path: str | Path | None = None,
        hazard_model_path: str | Path | None = None,
        football_data_model_path: str | Path | None = None,
    ) -> None:
        direct_path = Path(direct_model_path or os.getenv("ARCHIVE_FOUNDATION_MODEL", "models/archive_foundation.pkl"))
        hazard_path = Path(hazard_model_path or os.getenv("ARCHIVE_HAZARD_MODEL", "models/archive_hazard.pkl"))
        football_data_path = Path(
            football_data_model_path
            or os.getenv("FOOTBALL_DATA_GOAL_MODEL", "models/football_data_goal_models.pkl")
        )

        self.direct = ArchiveFoundationPredictor(direct_path)
        with hazard_path.open("rb") as handle:
            self.hazard_bundle = pickle.load(handle)

        self.football_data_bundle: dict[str, Any] | None = None
        if football_data_path.exists():
            with football_data_path.open("rb") as handle:
                self.football_data_bundle = pickle.load(handle)

    def _football_data_probabilities(self, record: dict[str, Any]) -> dict[str, float | None]:
        output = {"over_2_5": None, "both_teams_to_score": None}
        match = record.get("match") or {}
        if not bool(match.get("is_halftime")) or self.football_data_bundle is None:
            return output

        heads = self.football_data_bundle.get("heads") or {}
        mapping = {
            "over_2_5": "over_2_5_ht",
            "both_teams_to_score": "both_teams_to_score_ht",
        }
        for live_name, trained_name in mapping.items():
            head = heads.get(trained_name)
            if head is None:
                continue
            features = list(getattr(head, "feature_columns", []))
            if not features:
                continue
            frame = _football_data_halftime_frame(record, features)
            probability = float(head.predict_proba(frame)[0])
            if np.isfinite(probability):
                output[live_name] = probability
        return output

    def predict(self, record: dict[str, Any]) -> dict[str, Any]:
        direct = self.direct.predict(record)
        hazard = hazard_probabilities(self.hazard_bundle, live_foundation_features(record))
        football_data = self._football_data_probabilities(record)

        blended: dict[str, float | None] = {}
        disagreement: dict[str, float | None] = {}
        for head in ("another_goal", "goal_before_ht"):
            d = direct.get(head)
            h = hazard.get(head)
            if d is None or h is None:
                blended[head] = None
                disagreement[head] = None
            else:
                blended[head] = float((float(d) + float(h)) / 2.0)
                disagreement[head] = abs(float(d) - float(h))

        for head in ("over_2_5", "both_teams_to_score"):
            p = football_data.get(head)
            blended[head] = None if p is None else float(p)
            disagreement[head] = None if p is None else 0.0

        return {
            "direct": direct,
            "hazard": hazard,
            "football_data": football_data,
            "blended": blended,
            "disagreement": disagreement,
        }
