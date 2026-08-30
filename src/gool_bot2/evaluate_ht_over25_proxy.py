from __future__ import annotations

import argparse
import json
import pickle
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.metrics import brier_score_loss, log_loss, roc_auc_score


def _safe_auc(y: np.ndarray, p: np.ndarray) -> float | None:
    return float(roc_auc_score(y, p)) if len(np.unique(y)) > 1 else None


def evaluate(dataset_path: Path, model_path: Path) -> dict[str, object]:
    frame = pd.read_csv(dataset_path, parse_dates=["kickoff_at"])
    with model_path.open("rb") as handle:
        bundle = pickle.load(handle)

    head = bundle["heads"]["two_plus_goals_second_half"]

    # At 45' with exactly one first-half goal (1-0 or 0-1), final Over 2.5
    # is exactly the same event as scoring at least two goals in the second half.
    segment = frame[
        frame["two_plus_goals_second_half"].notna()
        & frame["period"].eq(1)
        & frame["minute"].between(44.5, 45.0)
        & frame["total_goals"].eq(1)
        & frame["score_diff"].abs().eq(1)
    ].copy()

    if segment.empty:
        raise RuntimeError("No halftime 1-0/0-1 rows found in prepared archive dataset")

    y = segment["two_plus_goals_second_half"].astype(int).to_numpy()
    p = np.clip(head.predict_proba(segment), 1e-6, 1 - 1e-6)

    by_score: dict[str, object] = {}
    for home_score, away_score in ((1, 0), (0, 1)):
        part = segment[
            segment["home_score"].eq(home_score) & segment["away_score"].eq(away_score)
        ]
        if part.empty:
            continue
        py = part["two_plus_goals_second_half"].astype(int).to_numpy()
        pp = np.clip(head.predict_proba(part), 1e-6, 1 - 1e-6)
        by_score[f"{home_score}-{away_score}"] = {
            "matches": int(part["match_id"].nunique()),
            "positive_rate": float(np.mean(py)),
            "roc_auc": _safe_auc(py, pp),
            "brier_score": float(brier_score_loss(py, pp)),
        }

    return {
        "segment": "halftime_one_goal_lead",
        "definition": "45' snapshot with score 1-0 or 0-1",
        "market_interpretation": "On this segment, final Over 2.5 is equivalent to >=2 second-half goals.",
        "uses_real_market_odds": False,
        "matches": int(segment["match_id"].nunique()),
        "rows": int(len(segment)),
        "positive_rate": float(np.mean(y)),
        "roc_auc": _safe_auc(y, p),
        "brier_score": float(brier_score_loss(y, p)),
        "log_loss": float(log_loss(y, p, labels=[0, 1])),
        "by_score": by_score,
    }


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Evaluate the 2+ second-half-goals head on the HT 1-0/0-1 Over 2.5-equivalent segment"
    )
    parser.add_argument("--dataset", default="data/foundation_bootstrap/processed/archive_training.csv")
    parser.add_argument("--model", default="data/foundation_bootstrap/models/archive_foundation.pkl")
    parser.add_argument(
        "--output",
        default="data/foundation_bootstrap/artifacts/ht_one_goal_over25_proxy_metrics.json",
    )
    args = parser.parse_args()

    metrics = evaluate(Path(args.dataset), Path(args.model))
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(metrics, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(metrics, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
