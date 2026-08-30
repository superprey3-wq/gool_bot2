from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import numpy as np
import pandas as pd
from sklearn.ensemble import ExtraTreesClassifier, HistGradientBoostingClassifier
from sklearn.impute import SimpleImputer
from sklearn.linear_model import LogisticRegression
from sklearn.pipeline import make_pipeline

from .archive_dataset import BASE_FEATURE_COLUMNS
from .train_archive_models import ProbabilityCalibrator, _match_split, _metrics


TARGET = "goal_before_ht"


def _zero_zero_rows(frame: pd.DataFrame) -> pd.DataFrame:
    """Live snapshots where the first half is still 0-0.

    The label is 1 only when at least one first-half goal occurs strictly after
    the snapshot. This keeps the task aligned with the live question:
    0-0 now -> will there be a goal before halftime?
    """
    rows = frame[
        (frame["period"] == 1)
        & (frame["home_score"] == 0)
        & (frame["away_score"] == 0)
        & frame[TARGET].notna()
    ].copy()
    return rows


def _cohort_summary(rows: pd.DataFrame) -> dict[str, Any]:
    per_match = (
        rows.sort_values(["match_id", "minute"])
        .drop_duplicates("match_id")
        [["match_id", "halftime_home_score", "halftime_away_score"]]
        .copy()
    )
    ht_goals = per_match["halftime_home_score"].fillna(0) + per_match["halftime_away_score"].fillna(0)
    ht_zero_ids = set(per_match.loc[ht_goals == 0, "match_id"])
    ht_goal_ids = set(per_match.loc[ht_goals > 0, "match_id"])
    zero_rows = rows[rows["match_id"].isin(ht_zero_ids)]
    goal_rows = rows[rows["match_id"].isin(ht_goal_ids)]
    return {
        "halftime_0_0": {
            "matches": int(len(ht_zero_ids)),
            "rows": int(len(zero_rows)),
            "positive_rows": int(zero_rows[TARGET].fillna(0).sum()),
        },
        "halftime_has_goal": {
            "matches": int(len(ht_goal_ids)),
            "rows": int(len(goal_rows)),
            "positive_rows": int(goal_rows[TARGET].fillna(0).sum()),
        },
    }


def _candidate_estimators() -> dict[str, Any]:
    return {
        "hist_gradient_boosting": HistGradientBoostingClassifier(
            learning_rate=.05,
            max_iter=350,
            max_leaf_nodes=31,
            l2_regularization=.2,
            random_state=42,
        ),
        "logistic_regression": make_pipeline(
            SimpleImputer(strategy="median"),
            LogisticRegression(max_iter=1500, C=.5, solver="lbfgs"),
        ),
        "extra_trees": make_pipeline(
            SimpleImputer(strategy="median"),
            ExtraTreesClassifier(
                n_estimators=350,
                min_samples_leaf=12,
                max_features="sqrt",
                class_weight="balanced",
                random_state=42,
                n_jobs=-1,
            ),
        ),
    }


def train_zero_zero_first_half(frame: pd.DataFrame) -> dict[str, Any]:
    frame = frame.copy()
    frame["kickoff_at"] = pd.to_datetime(frame["kickoff_at"], utc=True)
    rows = _zero_zero_rows(frame)
    if rows["match_id"].nunique() < 20:
        raise ValueError("Need at least 20 matches with 0-0 first-half snapshots")

    train, calibration, test = _match_split(rows)
    features = list(BASE_FEATURE_COLUMNS)
    y_train = train[TARGET].astype(int).to_numpy()
    y_cal = calibration[TARGET].astype(int).to_numpy()
    y_test = test[TARGET].astype(int).to_numpy()
    if len(np.unique(y_train)) < 2 or len(np.unique(y_cal)) < 2:
        raise ValueError("0-0 first-half split must contain both target classes")

    candidates: dict[str, Any] = {}
    best_name: str | None = None
    best_cal_auc = -1.0
    for name, model in _candidate_estimators().items():
        model.fit(train[features], y_train)
        raw_cal = model.predict_proba(calibration[features])[:, 1]
        calibrator = ProbabilityCalibrator().fit(raw_cal, y_cal)
        cal_p = calibrator.predict(raw_cal)
        cal_metrics = _metrics(y_cal, cal_p)
        raw_test = model.predict_proba(test[features])[:, 1]
        test_p = calibrator.predict(raw_test)
        test_metrics = _metrics(y_test, test_p)
        candidates[name] = {
            "model": model,
            "calibrator": calibrator,
            "calibration_metrics": cal_metrics,
            "test_metrics": test_metrics,
        }
        cal_auc = cal_metrics.get("roc_auc")
        if cal_auc is not None and float(cal_auc) > best_cal_auc:
            best_cal_auc = float(cal_auc)
            best_name = name

    if best_name is None:
        raise ValueError("Could not select a first-half model")

    return {
        "format_version": 1,
        "trained_at": datetime.now(timezone.utc).isoformat(),
        "task": "score_0_0_now_to_goal_before_halftime",
        "target": TARGET,
        "feature_columns": features,
        "cohorts": _cohort_summary(rows),
        "match_counts": {
            "train": int(train["match_id"].nunique()),
            "calibration": int(calibration["match_id"].nunique()),
            "test": int(test["match_id"].nunique()),
        },
        "row_counts": {
            "train": int(len(train)),
            "calibration": int(len(calibration)),
            "test": int(len(test)),
        },
        "best_model": best_name,
        "best_head": candidates[best_name],
        "candidates": candidates,
    }


def metrics_only(bundle: dict[str, Any]) -> dict[str, Any]:
    return {
        "trained_at": bundle["trained_at"],
        "task": bundle["task"],
        "target": bundle["target"],
        "cohorts": bundle["cohorts"],
        "match_counts": bundle["match_counts"],
        "row_counts": bundle["row_counts"],
        "best_model": bundle["best_model"],
        "candidates": {
            name: {
                "calibration_metrics": candidate["calibration_metrics"],
                "test_metrics": candidate["test_metrics"],
            }
            for name, candidate in bundle["candidates"].items()
        },
    }
