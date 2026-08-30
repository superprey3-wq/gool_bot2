from __future__ import annotations

import argparse
import json
import shutil
import urllib.request
import zipfile
from datetime import datetime, timezone
from pathlib import Path

from .archive_dataset import build_dataframe
from .archive_wyscout import import_wyscout
from .train_archive_models import _jsonable_metrics, train_archive_models
from .train_hazard_models import train_hazard_models


WYSCOUT_EVENTS_URL = "https://raw.githubusercontent.com/koenvo/wyscout-soccer-match-event-dataset/main/raw_data/events.zip"
WYSCOUT_MATCHES_URL = "https://raw.githubusercontent.com/koenvo/wyscout-soccer-match-event-dataset/main/raw_data/matches.zip"


def _download(url: str, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists() and path.stat().st_size > 0:
        return
    request = urllib.request.Request(url, headers={"User-Agent": "gool_bot2-foundation/1.0"})
    with urllib.request.urlopen(request, timeout=120) as response, path.open("wb") as handle:
        shutil.copyfileobj(response, handle)


def _extract(path: Path, output: Path) -> None:
    output.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(path) as archive:
        archive.extractall(output)


def _save_pickle(bundle: object, path: Path) -> None:
    import pickle

    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("wb") as handle:
        pickle.dump(bundle, handle, protocol=pickle.HIGHEST_PROTOCOL)


def run(work_dir: Path, step: int = 2, limit: int | None = None) -> dict[str, object]:
    raw = work_dir / "raw" / "wyscout"
    extracted = raw / "extracted"
    archive_jsonl = work_dir / "raw" / "open_event_archive.jsonl"
    processed = work_dir / "processed" / "archive_training.csv"
    models = work_dir / "models"
    artifacts = work_dir / "artifacts"

    events_zip = raw / "events.zip"
    matches_zip = raw / "matches.zip"
    _download(WYSCOUT_EVENTS_URL, events_zip)
    _download(WYSCOUT_MATCHES_URL, matches_zip)
    _extract(events_zip, extracted)
    _extract(matches_zip, extracted)

    imported = import_wyscout(extracted, archive_jsonl, limit=limit)
    frame = build_dataframe(archive_jsonl, cutoffs=range(1, 91, max(1, int(step))))
    if frame.empty:
        raise RuntimeError("Wyscout import produced no training rows")
    processed.parent.mkdir(parents=True, exist_ok=True)
    frame.to_csv(processed, index=False)

    direct = train_archive_models(frame)
    hazard = train_hazard_models(frame)
    _save_pickle(direct, models / "archive_foundation.pkl")
    _save_pickle(hazard, models / "archive_hazard.pkl")

    direct_metrics = _jsonable_metrics(direct)
    hazard_metrics = {
        "trained_at": hazard["trained_at"],
        "match_counts": hazard["match_counts"],
        "heads": {name: head.metrics for name, head in hazard["heads"].items()},
    }
    artifacts.mkdir(parents=True, exist_ok=True)
    (artifacts / "archive_foundation_metrics.json").write_text(
        json.dumps(direct_metrics, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (artifacts / "archive_hazard_metrics.json").write_text(
        json.dumps(hazard_metrics, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    summary = {
        "completed_at": datetime.now(timezone.utc).isoformat(),
        "source": "wyscout_open_event_dataset",
        "new_matches_imported": imported,
        "matches": int(frame["match_id"].nunique()),
        "training_rows": int(len(frame)),
        "step_minutes": int(step),
        "direct": direct_metrics,
        "hazard": hazard_metrics,
        "paths": {
            "dataset": str(processed),
            "foundation_model": str(models / "archive_foundation.pkl"),
            "hazard_model": str(models / "archive_hazard.pkl"),
        },
    }
    (artifacts / "foundation_bootstrap_summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return summary


def main() -> None:
    parser = argparse.ArgumentParser(description="Download Wyscout open events and train the first real GOOL foundation models")
    parser.add_argument("--work-dir", default="data/foundation_bootstrap")
    parser.add_argument("--step", type=int, default=2)
    parser.add_argument("--limit", type=int, default=None)
    args = parser.parse_args()
    summary = run(Path(args.work_dir), step=args.step, limit=args.limit)
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
