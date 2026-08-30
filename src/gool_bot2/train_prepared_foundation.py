from __future__ import annotations

import argparse
import json
import pickle
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

from .train_archive_models import _jsonable_metrics, train_archive_models, train_football_data_models
from .train_hazard_models import train_hazard_models


def _save_pickle(bundle: object, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("wb") as handle:
        pickle.dump(bundle, handle, protocol=pickle.HIGHEST_PROTOCOL)


def run(work_dir: Path) -> dict[str, object]:
    processed = work_dir / "processed" / "archive_training.csv"
    football_data_csv = work_dir / "processed" / "football_data_htft.csv"
    models = work_dir / "models"
    artifacts = work_dir / "artifacts"
    if not processed.exists():
        raise RuntimeError(f"Prepared dataset not found: {processed}")

    frame = pd.read_csv(processed)
    if frame.empty:
        raise RuntimeError("Prepared dataset is empty")
    frame["kickoff_at"] = pd.to_datetime(frame["kickoff_at"], utc=True)

    direct = train_archive_models(frame)
    hazard = train_hazard_models(frame)
    _save_pickle(direct, models / "archive_foundation.pkl")
    _save_pickle(hazard, models / "archive_hazard.pkl")

    football_data = None
    football_data_metrics = None
    if football_data_csv.exists():
        football_frame = pd.read_csv(football_data_csv)
        if not football_frame.empty:
            football_frame["kickoff_at"] = pd.to_datetime(football_frame["kickoff_at"], utc=True)
            football_data = train_football_data_models(football_frame)
            football_data_metrics = _jsonable_metrics(football_data)
            _save_pickle(football_data, models / "football_data_goal_models.pkl")

    direct_metrics = _jsonable_metrics(direct)
    hazard_metrics = {
        "trained_at": hazard["trained_at"],
        "match_counts": hazard["match_counts"],
        "heads": {name: head.metrics for name, head in hazard["heads"].items()},
    }

    # Hybrid bundle keeps the event models (StatsBomb + Wyscout) and the large
    # HT/FT Football-Data experts together. We deliberately do not average weak
    # Football-Data heads into stronger event heads without validation. Instead
    # the routing manifest uses the best source for each situation and keeps the
    # auxiliary experts available as contextual priors for later calibrated blending.
    hybrid = {
        "format_version": 1,
        "trained_at": datetime.now(timezone.utc).isoformat(),
        "event_direct": direct,
        "event_hazard": hazard,
        "football_data": football_data,
        "routing": {
            "another_goal_live": {"primary": "event_direct:another_goal", "auxiliary": "football_data:another_goal_ht"},
            "goal_before_ht_live": {"primary": "event_direct:goal_before_ht", "auxiliary": "football_data:goal_before_ht_prematch"},
            "over_2_5_live": {"primary": "event_direct:over_2_5", "auxiliary": "football_data:over_2_5_ht"},
            "over_2_5_halftime": {"primary": "football_data:over_2_5_ht", "auxiliary": "event_direct:over_2_5"},
        },
    }
    _save_pickle(hybrid, models / "hybrid_goal_models.pkl")

    artifacts.mkdir(parents=True, exist_ok=True)
    (artifacts / "archive_foundation_metrics.json").write_text(
        json.dumps(direct_metrics, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (artifacts / "archive_hazard_metrics.json").write_text(
        json.dumps(hazard_metrics, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    if football_data_metrics is not None:
        (artifacts / "football_data_goal_metrics.json").write_text(
            json.dumps(football_data_metrics, ensure_ascii=False, indent=2), encoding="utf-8"
        )
    (artifacts / "hybrid_routing.json").write_text(
        json.dumps(hybrid["routing"], ensure_ascii=False, indent=2), encoding="utf-8"
    )

    summary = {
        "phase": "train_hybrid",
        "completed_at": datetime.now(timezone.utc).isoformat(),
        "matches": int(frame["match_id"].nunique()),
        "training_rows": int(len(frame)),
        "direct": direct_metrics,
        "hazard": hazard_metrics,
        "football_data": football_data_metrics,
        "hybrid_routing": hybrid["routing"],
        "paths": {
            "dataset": str(processed),
            "football_data_dataset": str(football_data_csv) if football_data_csv.exists() else None,
            "foundation_model": str(models / "archive_foundation.pkl"),
            "hazard_model": str(models / "archive_hazard.pkl"),
            "football_data_model": str(models / "football_data_goal_models.pkl") if football_data is not None else None,
            "hybrid_model": str(models / "hybrid_goal_models.pkl"),
        },
    }
    (artifacts / "foundation_bootstrap_summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return summary


def main() -> None:
    parser = argparse.ArgumentParser(description="Train hybrid GOOL foundation from StatsBomb, Wyscout and Football-Data")
    parser.add_argument("--work-dir", default="data/foundation_bootstrap")
    args = parser.parse_args()
    print(json.dumps(run(Path(args.work_dir)), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
