from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path

from .archive_dataset import build_dataframe
from .archive_wyscout import import_wyscout
from .bootstrap_foundation import WYSCOUT_EVENTS_URL, WYSCOUT_MATCHES_URL, _download, _extract, _save_pickle
from .train_archive_models import _jsonable_metrics, train_archive_models
from .train_hazard_models import train_hazard_models


def run(work_dir: Path, step: int = 2, limit: int | None = None) -> dict[str, object]:
    raw = work_dir / "raw" / "wyscout"
    extracted = raw / "extracted"
    archive_jsonl = work_dir / "raw" / "wyscout_archive.jsonl"
    processed = work_dir / "processed" / "wyscout_training.csv"
    models = work_dir / "models"
    artifacts = work_dir / "artifacts"
    artifacts.mkdir(parents=True, exist_ok=True)
    archive_jsonl.unlink(missing_ok=True)

    events_zip = raw / "events.zip"
    matches_zip = raw / "matches.zip"
    _download(WYSCOUT_EVENTS_URL, events_zip)
    _download(WYSCOUT_MATCHES_URL, matches_zip)
    _extract(events_zip, extracted)
    _extract(matches_zip, extracted)

    imported = import_wyscout(extracted, archive_jsonl, limit=limit)
    frame = build_dataframe(archive_jsonl, cutoffs=range(1, 91, max(1, int(step))))
    if frame.empty:
        raise RuntimeError("Wyscout produced no training rows")
    processed.parent.mkdir(parents=True, exist_ok=True)
    frame.to_csv(processed, index=False)

    direct = train_archive_models(frame)
    hazard = train_hazard_models(frame)
    _save_pickle(direct, models / "archive_foundation_wyscout.pkl")
    _save_pickle(hazard, models / "archive_hazard_wyscout.pkl")

    direct_metrics = _jsonable_metrics(direct)
    hazard_metrics = {
        "trained_at": hazard["trained_at"],
        "match_counts": hazard["match_counts"],
        "heads": {name: head.metrics for name, head in hazard["heads"].items()},
    }
    summary = {
        "completed_at": datetime.now(timezone.utc).isoformat(),
        "source": "wyscout_only",
        "matches_imported": int(imported),
        "matches": int(frame["match_id"].nunique()),
        "training_rows": int(len(frame)),
        "step_minutes": int(step),
        "direct": direct_metrics,
        "hazard": hazard_metrics,
    }
    (artifacts / "wyscout_training_summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return summary


def main() -> None:
    parser = argparse.ArgumentParser(description="Train GOOL foundation models from Wyscout only")
    parser.add_argument("--work-dir", default="data/wyscout_foundation")
    parser.add_argument("--step", type=int, default=2)
    parser.add_argument("--limit", type=int, default=None)
    args = parser.parse_args()
    print(json.dumps(run(Path(args.work_dir), args.step, args.limit), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
