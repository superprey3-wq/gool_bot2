from __future__ import annotations

import argparse
import json
import math
import pickle
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
from sklearn.ensemble import HistGradientBoostingRegressor
from sklearn.metrics import mean_absolute_error, mean_poisson_deviance

from .archive_dataset import BASE_FEATURE_COLUMNS, COUNT_TARGET_COLUMNS


def poisson_at_least_one(lam: float) -> float:
    lam = max(0.0, float(lam))
    return 1.0 - math.exp(-lam)


def poisson_at_least_two(lam: float) -> float:
    lam = max(0.0, float(lam))
    return 1.0 - math.exp(-lam) * (1.0 + lam)


@dataclass
class HazardHead:
    target: str
    feature_columns: list[str]
    model: HistGradientBoostingRegressor
    metrics: dict[str, float]

    def predict_lambda(self, frame: pd.DataFrame) -> np.ndarray:
        return np.clip(self.model.predict(frame[self.feature_columns]), 1e-6, None)


def _split_matches(frame: pd.DataFrame, train_fraction: float = 0.80):
    matches = (
        frame[["match_id", "kickoff_at"]]
        .drop_duplicates("match_id")
        .sort_values(["kickoff_at", "match_id"])
        .reset_index(drop=True)
    )
    if len(matches) < 20:
        raise ValueError("Need at least 20 archived matches for chronological hazard validation")
    cutoff = min(len(matches) - 1, max(1, int(len(matches) * train_fraction)))
    train_ids = set(matches.iloc[:cutoff]["match_id"])
    test_ids = set(matches.iloc[cutoff:]["match_id"])
    return frame[frame["match_id"].isin(train_ids)].copy(), frame[frame["match_id"].isin(test_ids)].copy()


def _fit_head(train: pd.DataFrame, test: pd.DataFrame, target: str) -> HazardHead:
    train = train[train[target].notna()].copy()
    test = test[test[target].notna()].copy()
    if train.empty or test.empty:
        raise ValueError(f"Not enough rows for hazard target {target}")

    y_train = train[target].astype(float).to_numpy()
    y_test = test[target].astype(float).to_numpy()
    model = HistGradientBoostingRegressor(
        loss="poisson",
        learning_rate=0.05,
        max_iter=250,
        max_leaf_nodes=31,
        l2_regularization=0.1,
        random_state=42,
    )
    model.fit(train[BASE_FEATURE_COLUMNS], y_train)
    pred = np.clip(model.predict(test[BASE_FEATURE_COLUMNS]), 1e-6, None)
    metrics = {
        "mae_lambda": float(mean_absolute_error(y_test, pred)),
        "mean_poisson_deviance": float(mean_poisson_deviance(y_test, pred)),
        "mean_true_count": float(np.mean(y_test)),
        "mean_predicted_lambda": float(np.mean(pred)),
        "rows": float(len(y_test)),
    }
    return HazardHead(target=target, feature_columns=list(BASE_FEATURE_COLUMNS), model=model, metrics=metrics)


def train_hazard_models(frame: pd.DataFrame) -> dict[str, Any]:
    frame = frame.copy()
    frame["kickoff_at"] = pd.to_datetime(frame["kickoff_at"], utc=True)
    train, test = _split_matches(frame)
    heads = {target: _fit_head(train, test, target) for target in COUNT_TARGET_COLUMNS}
    return {
        "format_version": 1,
        "trained_at": datetime.now(timezone.utc).isoformat(),
        "feature_columns": list(BASE_FEATURE_COLUMNS),
        "heads": heads,
        "match_counts": {
            "train": int(train["match_id"].nunique()),
            "test": int(test["match_id"].nunique()),
        },
    }


def hazard_probabilities(bundle: dict[str, Any], frame: pd.DataFrame) -> dict[str, float]:
    heads: dict[str, HazardHead] = bundle["heads"]
    future_lambda = float(heads["future_goals_count"].predict_lambda(frame)[0])
    first_half_lambda = float(heads["future_first_half_goals"].predict_lambda(frame)[0])
    second_half_lambda = float(heads["second_half_goals_total"].predict_lambda(frame)[0])
    return {
        "lambda_future_goals": future_lambda,
        "lambda_future_first_half": first_half_lambda,
        "lambda_second_half_total": second_half_lambda,
        "another_goal": poisson_at_least_one(future_lambda),
        "goal_before_ht": poisson_at_least_one(first_half_lambda),
        "two_plus_goals_second_half": poisson_at_least_two(second_half_lambda),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Train GOOL remaining-goal Poisson hazard experts")
    parser.add_argument("--input", default="data/processed/archive_training.csv")
    parser.add_argument("--model-output", default="models/archive_hazard.pkl")
    parser.add_argument("--metrics-output", default="artifacts/archive_hazard_metrics.json")
    args = parser.parse_args()

    frame = pd.read_csv(args.input, parse_dates=["kickoff_at"])
    bundle = train_hazard_models(frame)

    model_output = Path(args.model_output)
    model_output.parent.mkdir(parents=True, exist_ok=True)
    with model_output.open("wb") as handle:
        pickle.dump(bundle, handle, protocol=pickle.HIGHEST_PROTOCOL)

    metrics = {
        "trained_at": bundle["trained_at"],
        "match_counts": bundle["match_counts"],
        "heads": {name: head.metrics for name, head in bundle["heads"].items()},
    }
    metrics_output = Path(args.metrics_output)
    metrics_output.parent.mkdir(parents=True, exist_ok=True)
    metrics_output.write_text(json.dumps(metrics, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(metrics, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
