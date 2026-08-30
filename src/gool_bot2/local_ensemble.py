from __future__ import annotations

import os
import pickle
from pathlib import Path
from typing import Any

from .foundation_inference import ArchiveFoundationPredictor, live_foundation_features
from .train_hazard_models import hazard_probabilities


class LocalFootballEnsemble:
    """Local, API-free GOOL model stack for the Monkey server.

    Direct classifiers and remaining-goal hazard experts are kept separate in
    the output. `blended` is a temporary equal-weight research consensus until a
    chronological OOF meta-model is trained; it must not be mistaken for a
    separately calibrated production model.
    """

    def __init__(
        self,
        direct_model_path: str | Path | None = None,
        hazard_model_path: str | Path | None = None,
    ) -> None:
        direct_path = Path(direct_model_path or os.getenv("ARCHIVE_FOUNDATION_MODEL", "models/archive_foundation.pkl"))
        hazard_path = Path(hazard_model_path or os.getenv("ARCHIVE_HAZARD_MODEL", "models/archive_hazard.pkl"))
        self.direct = ArchiveFoundationPredictor(direct_path)
        with hazard_path.open("rb") as handle:
            self.hazard_bundle = pickle.load(handle)

    def predict(self, record: dict[str, Any]) -> dict[str, Any]:
        direct = self.direct.predict(record)
        hazard = hazard_probabilities(self.hazard_bundle, live_foundation_features(record))

        blended: dict[str, float | None] = {}
        disagreement: dict[str, float | None] = {}
        for head in ("another_goal", "goal_before_ht", "two_plus_goals_second_half"):
            d = direct.get(head)
            h = hazard.get(head)
            if d is None or h is None:
                blended[head] = None
                disagreement[head] = None
            else:
                blended[head] = float((float(d) + float(h)) / 2.0)
                disagreement[head] = abs(float(d) - float(h))
        return {
            "direct": direct,
            "hazard": hazard,
            "blended": blended,
            "disagreement": disagreement,
        }
