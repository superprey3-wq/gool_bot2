from __future__ import annotations

import argparse
import json
import pickle
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import brier_score_loss, log_loss, roc_auc_score

from .archive_dataset import BASE_FEATURE_COLUMNS, TARGET_COLUMNS
from .archive_football_data import PRIOR_COLUMNS


@dataclass
class ProbabilityCalibrator:
    model: LogisticRegression | None = None

    def fit(self, probabilities: np.ndarray, y: np.ndarray) -> "ProbabilityCalibrator":
        p = np.clip(np.asarray(probabilities, dtype=float), 1e-6, 1 - 1e-6)
        y = np.asarray(y, dtype=int)
        if len(np.unique(y)) < 2:
            self.model = None
            return self
        logits = np.log(p / (1 - p)).reshape(-1, 1)
        self.model = LogisticRegression(solver="lbfgs").fit(logits, y)
        return self

    def predict(self, probabilities: np.ndarray) -> np.ndarray:
        p = np.clip(np.asarray(probabilities, dtype=float), 1e-6, 1 - 1e-6)
        if self.model is None:
            return p
        return self.model.predict_proba(np.log(p / (1 - p)).reshape(-1, 1))[:, 1]


@dataclass
class TrainedHead:
    target: str
    feature_columns: list[str]
    model: HistGradientBoostingClassifier
    calibrator: ProbabilityCalibrator
    metrics: dict[str, float | None]

    def predict_proba(self, frame: pd.DataFrame) -> np.ndarray:
        return self.calibrator.predict(self.model.predict_proba(frame[self.feature_columns])[:, 1])


def _match_split(frame: pd.DataFrame, train_fraction: float = .70, calibration_fraction: float = .15):
    matches = frame[["match_id", "kickoff_at"]].drop_duplicates("match_id").sort_values(["kickoff_at", "match_id"]).reset_index(drop=True)
    n = len(matches)
    if n < 20:
        raise ValueError("Need at least 20 archived matches")
    a = max(1, int(n * train_fraction))
    b = min(max(a + 1, int(n * (train_fraction + calibration_fraction))), n - 1)
    ids = [set(matches.iloc[:a].match_id), set(matches.iloc[a:b].match_id), set(matches.iloc[b:].match_id)]
    return tuple(frame[frame.match_id.isin(x)].copy() for x in ids)


def _metrics(y: np.ndarray, p: np.ndarray) -> dict[str, float | None]:
    p = np.clip(np.asarray(p, dtype=float), 1e-6, 1 - 1e-6)
    return {
        "log_loss": float(log_loss(y, p, labels=[0, 1])),
        "brier_score": float(brier_score_loss(y, p)),
        "roc_auc": float(roc_auc_score(y, p)) if len(np.unique(y)) > 1 else None,
        "positive_rate": float(np.mean(y)),
        "rows": float(len(y)),
    }


def _fit_head(
    train: pd.DataFrame,
    calibration: pd.DataFrame,
    test: pd.DataFrame,
    target: str,
    feature_columns: list[str] | None = None,
) -> TrainedHead:
    features = list(feature_columns or BASE_FEATURE_COLUMNS)
    train = train[train[target].notna()].copy()
    calibration = calibration[calibration[target].notna()].copy()
    test = test[test[target].notna()].copy()
    if min(len(train), len(calibration), len(test)) == 0:
        raise ValueError(f"Not enough rows for target {target}")

    y_train = train[target].astype(int).to_numpy()
    y_cal = calibration[target].astype(int).to_numpy()
    y_test = test[target].astype(int).to_numpy()
    if len(np.unique(y_train)) < 2:
        raise ValueError(f"Training target {target} has only one class")

    model = HistGradientBoostingClassifier(
        learning_rate=.06,
        max_iter=250,
        max_leaf_nodes=31,
        l2_regularization=.1,
        random_state=42,
    ).fit(train[features], y_train)
    raw_cal = model.predict_proba(calibration[features])[:, 1]
    calibrator = ProbabilityCalibrator().fit(raw_cal, y_cal)
    raw_test = model.predict_proba(test[features])[:, 1]
    p = calibrator.predict(raw_test)
    metrics = _metrics(y_test, p)
    metrics["raw_log_loss"] = float(log_loss(y_test, np.clip(raw_test, 1e-6, 1 - 1e-6), labels=[0, 1]))
    metrics["raw_brier_score"] = float(brier_score_loss(y_test, raw_test))
    return TrainedHead(target, features, model, calibrator, metrics)


def train_archive_models(frame: pd.DataFrame) -> dict[str, Any]:
    frame = frame.copy()
    frame["kickoff_at"] = pd.to_datetime(frame["kickoff_at"], utc=True)
    train, calibration, test = _match_split(frame)
    heads = {target: _fit_head(train, calibration, test, target) for target in TARGET_COLUMNS}
    return {
        "format_version": 3,
        "trained_at": datetime.now(timezone.utc).isoformat(),
        "feature_columns": list(BASE_FEATURE_COLUMNS),
        "heads": heads,
        "match_counts": {
            "train": int(train.match_id.nunique()),
            "calibration": int(calibration.match_id.nunique()),
            "test": int(test.match_id.nunique()),
        },
    }


def train_football_data_models(frame: pd.DataFrame) -> dict[str, Any]:
    """Train leakage-safe auxiliary models from Football-Data HT/FT history.

    `over_2_5_ht` and `another_goal_ht` are evaluated exactly at the halftime
    whistle using the known HT score plus rolling form calculated from earlier
    matches only. `goal_before_ht_prematch` intentionally excludes the current
    halftime score and serves as a historical prior for the live first-half
    event model.
    """
    frame = frame.copy()
    frame["kickoff_at"] = pd.to_datetime(frame["kickoff_at"], utc=True)
    train, calibration, test = _match_split(frame)

    ht_features = ["home_score", "away_score", "total_goals", "score_diff", *PRIOR_COLUMNS]
    prematch_features = list(PRIOR_COLUMNS)
    heads = {
        "over_2_5_ht": _fit_head(train, calibration, test, "over_2_5", ht_features),
        "another_goal_ht": _fit_head(train, calibration, test, "another_goal", ht_features),
        "goal_before_ht_prematch": _fit_head(train, calibration, test, "goal_in_first_half", prematch_features),
    }
    return {
        "format_version": 1,
        "trained_at": datetime.now(timezone.utc).isoformat(),
        "feature_columns": {"halftime": ht_features, "prematch": prematch_features},
        "heads": heads,
        "match_counts": {
            "train": int(train.match_id.nunique()),
            "calibration": int(calibration.match_id.nunique()),
            "test": int(test.match_id.nunique()),
        },
    }


def _jsonable_metrics(bundle: dict[str, Any]) -> dict[str, Any]:
    return {
        "trained_at": bundle["trained_at"],
        "match_counts": bundle["match_counts"],
        "heads": {name: head.metrics for name, head in bundle["heads"].items()},
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", default="data/processed/archive_training.csv")
    parser.add_argument("--model-output", default="models/archive_foundation.pkl")
    parser.add_argument("--metrics-output", default="artifacts/archive_foundation_metrics.json")
    args = parser.parse_args()
    frame = pd.read_csv(args.input, parse_dates=["kickoff_at"])
    bundle = train_archive_models(frame)
    out = Path(args.model_output)
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("wb") as handle:
        pickle.dump(bundle, handle, pickle.HIGHEST_PROTOCOL)
    metrics = Path(args.metrics_output)
    metrics.parent.mkdir(parents=True, exist_ok=True)
    metrics.write_text(json.dumps(_jsonable_metrics(bundle), ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(_jsonable_metrics(bundle), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
