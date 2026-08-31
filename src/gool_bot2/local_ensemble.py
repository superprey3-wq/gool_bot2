from __future__ import annotations

import os
import pickle
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from .archive_football_data import PRIOR_COLUMNS
from .foundation_inference import ArchiveFoundationPredictor, live_foundation_features
from .match_context import provider_pair
from .train_hazard_models import hazard_probabilities


LIVE_HEADS = ("another_goal", "goal_before_ht", "over_2_5", "both_teams_to_score")
GOAL_BEFORE_HT_BASELINE_AUC = 0.6946


def _football_data_halftime_frame(record: dict[str, Any], feature_columns: list[str]) -> pd.DataFrame:
    match = record.get("match") or {}
    home_score = int(match.get("home_score") or 0)
    away_score = int(match.get("away_score") or 0)
    row: dict[str, float] = {column: float("nan") for column in feature_columns}
    row.update({
        "home_score": float(home_score),
        "away_score": float(away_score),
        "total_goals": float(home_score + away_score),
        "score_diff": float(home_score - away_score),
    })
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


def _pair_total(record: dict[str, Any], key: str) -> float | None:
    home, away = provider_pair(record, key)
    if home is None or away is None:
        return None
    return float(home + away)


def _pair_side(record: dict[str, Any], key: str, side: int) -> float | None:
    home, away = provider_pair(record, key)
    value = home if side == 0 else away
    return None if value is None else float(value)


def _pressure_overlay(values: dict[str, float | None], expectations: dict[str, tuple[float, float]], cap: float) -> tuple[float | None, float]:
    components: list[tuple[float, float]] = []
    for key, (expected, weight) in expectations.items():
        value = values.get(key)
        if value is None or expected <= 0:
            continue
        ratio = max(0.0, min(2.25, float(value) / expected))
        components.append((ratio, weight))
    if not components:
        return None, 0.0
    weight_sum = sum(weight for _, weight in components)
    pressure = sum(value * weight for value, weight in components) / weight_sum
    adjustment = max(-cap, min(cap, (pressure - 1.0) * cap))
    return float(pressure), float(adjustment)


def _first_half_live_analysis(record: dict[str, Any]) -> dict[str, float | None]:
    """Legacy GOOL-style live pressure overlay for the trained goal-before-HT head.

    The old GOOL first-half engine gave most weight to live chance quality and
    pressure, while team/history context only strengthened or vetoed borderline
    cases. GOOL 2 keeps the trained direct/hazard models as the foundation and
    uses the same live-pressure idea only as a bounded overlay.
    """
    match = record.get("match") or {}
    minute = float(match.get("minute") or 0.0)
    home_score = int(match.get("home_score") or 0)
    away_score = int(match.get("away_score") or 0)
    is_halftime = bool(match.get("is_halftime"))
    output: dict[str, float | None] = {
        "pressure_score": None,
        "probability_adjustment": 0.0,
        "xg_total": _pair_total(record, "xg"),
        "xgot_total": _pair_total(record, "xgot"),
        "shots_total": _pair_total(record, "shots"),
        "sot_total": _pair_total(record, "shots_on_target"),
        "big_chances_total": _pair_total(record, "big_chances"),
        "corners_total": _pair_total(record, "corners"),
        "dangerous_attacks_total": _pair_total(record, "dangerous_attacks"),
        "baseline_auc": GOAL_BEFORE_HT_BASELINE_AUC,
    }
    if is_halftime or minute <= 0 or minute > 45 or home_score != 0 or away_score != 0:
        return output

    progress = max(0.08, min(1.0, minute / 45.0))
    expectations = {
        "xg_total": (1.15 * progress, 0.30),
        "xgot_total": (0.95 * progress, 0.10),
        "shots_total": (12.0 * progress, 0.14),
        "sot_total": (4.0 * progress, 0.20),
        "big_chances_total": (1.8 * progress, 0.12),
        "corners_total": (5.0 * progress, 0.06),
        "dangerous_attacks_total": (48.0 * progress, 0.08),
    }
    pressure, adjustment = _pressure_overlay(output, expectations, 0.08)
    output["pressure_score"] = pressure
    output["probability_adjustment"] = adjustment
    return output


def _second_half_two_goal_analysis(record: dict[str, Any]) -> dict[str, float | None]:
    """GOOL-style support layer for the HT 1:0/0:1 -> FT over 2.5 setup.

    This market intentionally needs two second-half goals. The trained Football-
    Data O2.5 model remains primary; first-half live activity only adjusts it by
    at most +/-8 percentage points.
    """
    match = record.get("match") or {}
    hs = int(match.get("home_score") or 0)
    aws = int(match.get("away_score") or 0)
    output: dict[str, float | None] = {
        "pressure_score": None,
        "probability_adjustment": 0.0,
        "xg_total": _pair_total(record, "xg"),
        "xgot_total": _pair_total(record, "xgot"),
        "shots_total": _pair_total(record, "shots"),
        "sot_total": _pair_total(record, "shots_on_target"),
        "big_chances_total": _pair_total(record, "big_chances"),
        "corners_total": _pair_total(record, "corners"),
        "dangerous_attacks_total": _pair_total(record, "dangerous_attacks"),
    }
    if not bool(match.get("is_halftime")) or (hs, aws) not in {(1, 0), (0, 1)}:
        return output
    expectations = {
        "xg_total": (1.15, 0.30),
        "xgot_total": (0.95, 0.10),
        "shots_total": (12.0, 0.14),
        "sot_total": (4.0, 0.20),
        "big_chances_total": (1.8, 0.12),
        "corners_total": (5.0, 0.06),
        "dangerous_attacks_total": (48.0, 0.08),
    }
    pressure, adjustment = _pressure_overlay(output, expectations, 0.08)
    output["pressure_score"] = pressure
    output["probability_adjustment"] = adjustment
    return output


def _btts_halftime_analysis(record: dict[str, Any]) -> dict[str, float | None]:
    """Small GOOL live overlay for BTTS when exactly one side has not scored at HT."""
    match = record.get("match") or {}
    hs = int(match.get("home_score") or 0)
    aws = int(match.get("away_score") or 0)
    output: dict[str, float | None] = {"threat_score": None, "probability_adjustment": 0.0}
    if not bool(match.get("is_halftime")) or (hs > 0 and aws > 0) or (hs == 0 and aws == 0):
        return output
    scoreless_side = 0 if hs == 0 else 1
    values = {
        "xg": _pair_side(record, "xg", scoreless_side),
        "shots": _pair_side(record, "shots", scoreless_side),
        "sot": _pair_side(record, "shots_on_target", scoreless_side),
        "big": _pair_side(record, "big_chances", scoreless_side),
        "danger": _pair_side(record, "dangerous_attacks", scoreless_side),
    }
    expectations = {
        "xg": (0.48, 0.38),
        "shots": (5.0, 0.16),
        "sot": (1.5, 0.25),
        "big": (0.7, 0.13),
        "danger": (22.0, 0.08),
    }
    threat, adjustment = _pressure_overlay(values, expectations, 0.06)
    output.update(values)
    output["threat_score"] = threat
    output["probability_adjustment"] = adjustment
    return output


class LocalFootballEnsemble:
    """Local, API-free GOOL model stack for the Monkey server."""

    def __init__(self, direct_model_path: str | Path | None = None, hazard_model_path: str | Path | None = None, football_data_model_path: str | Path | None = None) -> None:
        direct_path = Path(direct_model_path or os.getenv("ARCHIVE_FOUNDATION_MODEL", "models/archive_foundation.pkl"))
        hazard_path = Path(hazard_model_path or os.getenv("ARCHIVE_HAZARD_MODEL", "models/archive_hazard.pkl"))
        football_data_path = Path(football_data_model_path or os.getenv("FOOTBALL_DATA_GOAL_MODEL", "models/football_data_goal_models.pkl"))
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
        mapping = {"over_2_5": "over_2_5_ht", "both_teams_to_score": "both_teams_to_score_ht"}
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
        first_half_analysis = _first_half_live_analysis(record)
        second_half_analysis = _second_half_two_goal_analysis(record)
        btts_analysis = _btts_halftime_analysis(record)

        blended: dict[str, float | None] = {}
        disagreement: dict[str, float | None] = {}
        for head in ("another_goal", "goal_before_ht"):
            d = direct.get(head)
            h = hazard.get(head)
            if d is None or h is None:
                blended[head] = None
                disagreement[head] = None
            else:
                base_probability = float((float(d) + float(h)) / 2.0)
                if head == "goal_before_ht":
                    base_probability += float(first_half_analysis.get("probability_adjustment") or 0.0)
                blended[head] = float(max(0.01, min(0.99, base_probability)))
                disagreement[head] = abs(float(d) - float(h))

        over25 = football_data.get("over_2_5")
        if over25 is None:
            blended["over_2_5"] = None
            disagreement["over_2_5"] = None
        else:
            blended["over_2_5"] = float(max(0.01, min(0.99, float(over25) + float(second_half_analysis.get("probability_adjustment") or 0.0))))
            disagreement["over_2_5"] = 0.0

        btts = football_data.get("both_teams_to_score")
        if btts is None:
            blended["both_teams_to_score"] = None
            disagreement["both_teams_to_score"] = None
        else:
            blended["both_teams_to_score"] = float(max(0.01, min(0.99, float(btts) + float(btts_analysis.get("probability_adjustment") or 0.0))))
            disagreement["both_teams_to_score"] = 0.0

        return {
            "direct": direct,
            "hazard": hazard,
            "football_data": football_data,
            "first_half_analysis": first_half_analysis,
            "second_half_analysis": second_half_analysis,
            "btts_analysis": btts_analysis,
            "blended": blended,
            "disagreement": disagreement,
        }
