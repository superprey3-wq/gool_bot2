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


@dataclass
class ProbabilityCalibrator:
    """Simple sigmoid calibration fitted on a chronological calibration block."""

    model: LogisticRegression | None = None

    def fit(self, probabilities: np.ndarray, y: np.ndarray) -> "ProbabilityCalibrator":
        p = np.clip(np.asarray(probabilities, dtype=float), 1e-6, 1 - 1e-6)
        y = np.asarray(y, dtype=int)
        if len(np.unique(y)) < 2:
            self.model = None
            return self
        logits = np.log(p / (1.0 - p)).reshape(-1, 1)
        model = LogisticRegression(solver="lbfgs")
        model.fit(logits, y)
        self.model = model
        return self

    def predict(self, probabilities: np.ndarray) -> np.ndarray:
        p = np.clip(np.asarray(probabilities, dtype=float), 1e-6, 1 - 1e-6)
        if self.model is None:
            return p
        logits = np.log(p / (1.0 - p)).reshape(-1, 1)
        return self.model.predict_proba(logits)[:, 1]


@dataclass
class TrainedHead:
    target: str
    feature_columns: list[str]
    model: HistGradientBoostingClassifier
    calibrator: ProbabilityCalibrator
    metrics: dict[str, float | None]

    def predict_proba(self, frame: pd.DataFrame) -> np.ndarray:
        raw = self.model.predict_proba(frame[self.feature_columns])[:, 1]
        return self.calibrator.predict(raw)


def _match_split(frame: pd.DataFrame, train_fraction: float = 0.70, calibration_fraction: float = 0.15):
    """Split chronologically by complete matches so a match never crosses blocks."""
    matches = (
        frame[["match_id", "kickoff_at"]]
        .drop_duplicates("match_id")
        .sort_values(["kickoff_at", "match_id"])
        .reset_index(drop=True)
    )
    n = len(matches)
    if n < 20:
        raise ValueError("Need at least 20 archived matches for chronological train/calibration/test split")

    train_end = max(1, int(n * train_fraction))
    cal_end = max(train_end + 1, int(n * (train_fraction + calibration_fraction)))
    cal_end = min(cal_end, n - 1)

    train_ids = set(matches.iloc[:train_end]["match_id"])
    cal_ids = set(matches.iloc[train_end:cal_end]["match_id"])
    test_ids = set(matches.iloc[cal_end:]["match_id"])
    return (
        frame[frame["match_id"].isin(train_ids)].copy(),
        frame[frame["match_id"].isin(cal_ids)].copy(),
        frame[frame["match_id"].isin(test_ids)].copy(),
    )


def _safe_auc(y: np.ndarray, p: np.ndarray) -> float | None:
    return float(roc_auc_score(y, p)) if len(np.unique(y)) > 1 else None


def _metrics(y: np.ndarray, p: np.ndarray) -> dict[str, float | None]:
    p = np.clip(np.asarray(p, dtype=float), 1e-6, 1 - 1e-6)
    return {
        "log_loss": float(log_loss(y, p, labels=[0, 1])),
        "brier_score": float(brier_score_loss(y, p)),
        "roc_auc": _safe_auc(y, p),
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
    feature_columns = list(feature_columns or BASE_FEATURE_COLUMNS)
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
        learning_rate=0.06,
        max_iter=250,
        max_leaf_nodes=31,
        l2_regularization=0.1,
        random_state=42,
    )
    model.fit(train[feature_columns], y_train)

    raw_cal = model.predict_proba(calibration[feature_columns])[:, 1]
    calibrator = ProbabilityCalibrator().fit(raw_cal, y_cal)
    raw_test = model.predict_proba(test[feature_columns])[:, 1]
    calibrated_test = calibrator.predict(raw_test)

    metrics = _metrics(y_test, calibrated_test)
    metrics["raw_log_loss"] = float(log_loss(y_test, np.clip(raw_test, 1e-6, 1 - 1e-6), labels=[0, 1]))
    metrics["raw_brier_score"] = float(brier_score_loss(y_test, raw_test))
    return TrainedHead(
        target=target,
        feature_columns=feature_columns,
        model=model,
        calibrator=calibrator,
        metrics=metrics,
    )


def _halftime_snapshot(frame: pd.DataFrame) -> pd.DataFrame:
    """One post-whistle halftime row per match with the exact period-1 score.

    The generic archive is sampled at minute cutoffs, so stoppage-time goals can
    occur after the nominal 45' snapshot. The dedicated halftime head therefore
    replaces score fields with the exact score from all period-1 goals while
    keeping only information that is available by the halftime whistle.
    """
    required = {"halftime_home_score", "halftime_away_score", "two_plus_goals_second_half"}
    if not required.issubset(frame.columns):
        return pd.DataFrame(columns=frame.columns)

    half = frame[(frame["period"] == 1) & frame["two_plus_goals_second_half"].notna()].copy()
    if half.empty:
        return half
    half = half.sort_values(["match_id", "minute"]).groupby("match_id", as_index=False).tail(1).copy()
    half["home_score"] = half["halftime_home_score"].astype(float)
    half["away_score"] = half["halftime_away_score"].astype(float)
    half["total_goals"] = half["home_score"] + half["away_score"]
    half["score_diff"] = half["home_score"] - half["away_score"]
    half["minute"] = 45.0
    half["period"] = 1.0
    half["time_remaining_nominal"] = 45.0
    return half


def train_archive_models(frame: pd.DataFrame) -> dict[str, Any]:
    frame = frame.copy()
    frame["kickoff_at"] = pd.to_datetime(frame["kickoff_at"], utc=True)
    train, calibration, test = _match_split(frame)

    heads = {target: _fit_head(train, calibration, test, target) for target in TARGET_COLUMNS}

    # Dedicated halftime model: one sample per finished match, exact HT score,
    # first-half event/xG/shot context, target = 2+ goals in the second half.
    ht_train = _halftime_snapshot(train)
    ht_calibration = _halftime_snapshot(calibration)
    ht_test = _halftime_snapshot(test)
    if min(len(ht_train), len(ht_calibration), len(ht_test)) > 0:
        heads["two_plus_goals_second_half_ht"] = _fit_head(
            ht_train,
            ht_calibration,
            ht_test,
            "two_plus_goals_second_half",
        )
        heads["two_plus_goals_second_half_ht"].metrics["matches"] = float(len(ht_test))

    match_counts = {
        "train": int(train["match_id"].nunique()),
        "calibration": int(calibration["match_id"].nunique()),
        "test": int(test["match_id"].nunique()),
    }
    return {
        "format_version": 2,
        "trained_at": datetime.now(timezone.utc).isoformat(),
        "feature_columns": list(BASE_FEATURE_COLUMNS),
        "heads": heads,
        "match_counts": match_counts,
    }


def _jsonable_metrics(bundle: dict[str, Any]) -> dict[str, Any]:
    return {
        "trained_at": bundle["trained_at"],
        "match_counts": bundle["match_counts"],
        "heads": {name: head.metrics for name, head in bundle["heads"].items()},
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Train GOOL archive models with chronological calibration")
    parser.add_argument("--input", default="data/processed/archive_training.csv")
    parser.add_argument("--model-output", default="models/archive_foundation.pkl")
    parser.add_argument("--metrics-output", default="artifacts/archive_foundation_metrics.json")
    args = parser.parse_args()

    frame = pd.read_csv(args.input, parse_dates=["kickoff_at"])
    bundle = train_archive_models(frame)

    model_output = Path(args.model_output)
    model_output.parent.mkdir(parents=True, exist_ok=True)
    with model_output.open("wb") as handle:
        pickle.dump(bundle, handle, protocol=pickle.HIGHEST_PROTOCOL)

    metrics_output = Path(args.metrics_output)
    metrics_output.parent.mkdir(parents=True, exist_ok=True)
    metrics_output.write_text(json.dumps(_jsonable_metrics(bundle), ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(_jsonable_metrics(bundle), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
