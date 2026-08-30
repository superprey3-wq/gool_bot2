from __future__ import annotations

import argparse
import json
import pickle
from pathlib import Path

import pandas as pd

from .archive_football_data import import_football_data
from .train_archive_models import _jsonable_metrics, train_football_data_models


def run(work_dir: Path) -> dict[str, object]:
    processed = work_dir / "processed" / "football_data_htft.csv"
    models = work_dir / "models"
    artifacts = work_dir / "artifacts"

    import_summary = import_football_data(processed)
    frame = pd.read_csv(processed)
    if frame.empty:
        raise RuntimeError("Football-Data dataset is empty")
    frame["kickoff_at"] = pd.to_datetime(frame["kickoff_at"], utc=True)

    bundle = train_football_data_models(frame)
    metrics = _jsonable_metrics(bundle)

    models.mkdir(parents=True, exist_ok=True)
    model_path = models / "football_data_goal_models.pkl"
    with model_path.open("wb") as handle:
        pickle.dump(bundle, handle, protocol=pickle.HIGHEST_PROTOCOL)

    artifacts.mkdir(parents=True, exist_ok=True)
    metrics_path = artifacts / "football_data_goal_metrics.json"
    metrics_path.write_text(json.dumps(metrics, ensure_ascii=False, indent=2), encoding="utf-8")

    summary = {
        "phase": "football_data_train",
        "import": import_summary,
        "targets": list(bundle["heads"].keys()),
        "metrics": metrics,
        "model": str(model_path),
        "metrics_file": str(metrics_path),
    }
    (artifacts / "football_data_training_summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return summary


def main() -> None:
    parser = argparse.ArgumentParser(description="Download Football-Data and train all configured GOOL target models")
    parser.add_argument("--work-dir", default="data/football_data_training")
    args = parser.parse_args()
    print(json.dumps(run(Path(args.work_dir)), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
