from __future__ import annotations

import argparse
import json
import shutil
from datetime import datetime, timezone
from pathlib import Path

from .archive_dataset import build_dataframe
from .archive_statsbomb_stream import import_statsbomb_stream
from .archive_wyscout import import_wyscout
from .bootstrap_foundation import WYSCOUT_EVENTS_URL, WYSCOUT_MATCHES_URL, _download, _extract, _save_pickle
from .train_archive_models import _jsonable_metrics, train_archive_models
from .train_hazard_models import train_hazard_models


def run(
    work_dir: Path,
    step: int = 3,
    statsbomb_limit: int | None = None,
    workers: int = 6,
) -> dict[str, object]:
    raw_root = work_dir / "raw"
    wyscout_raw = raw_root / "wyscout"
    extracted = wyscout_raw / "extracted"
    archive_jsonl = raw_root / "combined_archive.jsonl"
    processed = work_dir / "processed" / "combined_training.csv"
    models = work_dir / "models"
    artifacts = work_dir / "artifacts"
    artifacts.mkdir(parents=True, exist_ok=True)
    archive_jsonl.unlink(missing_ok=True)

    events_zip = wyscout_raw / "events.zip"
    matches_zip = wyscout_raw / "matches.zip"
    _download(WYSCOUT_EVENTS_URL, events_zip)
    _download(WYSCOUT_MATCHES_URL, matches_zip)
    _extract(events_zip, extracted)
    _extract(matches_zip, extracted)
    wyscout_imported = import_wyscout(extracted, archive_jsonl)
    print(f"wyscout imported={wyscout_imported}", flush=True)

    # Wyscout has already been normalized into the compact canonical JSONL.
    # Delete its source ZIPs/extracted files before StatsBomb to keep runner disk low.
    shutil.rmtree(wyscout_raw, ignore_errors=True)

    statsbomb = import_statsbomb_stream(
        archive_jsonl,
        limit=statsbomb_limit,
        workers=workers,
    )
    print("statsbomb=" + json.dumps(statsbomb, ensure_ascii=False), flush=True)

    frame = build_dataframe(archive_jsonl, cutoffs=range(1, 91, max(1, int(step))))
    if frame.empty:
        raise RuntimeError("Combined Wyscout + StatsBomb archive produced no training rows")
    processed.parent.mkdir(parents=True, exist_ok=True)
    frame.to_csv(processed, index=False)
    print(
        f"dataset matches={frame['match_id'].nunique()} rows={len(frame)} sources={frame.groupby('source')['match_id'].nunique().to_dict() if 'source' in frame.columns else {}}",
        flush=True,
    )

    direct = train_archive_models(frame)
    hazard = train_hazard_models(frame)
    _save_pickle(direct, models / "archive_foundation_wyscout_statsbomb.pkl")
    _save_pickle(hazard, models / "archive_hazard_wyscout_statsbomb.pkl")

    direct_metrics = _jsonable_metrics(direct)
    hazard_metrics = {
        "trained_at": hazard["trained_at"],
        "match_counts": hazard["match_counts"],
        "heads": {name: head.metrics for name, head in hazard["heads"].items()},
    }
    source_counts = (
        {str(source): int(count) for source, count in frame.groupby("source")["match_id"].nunique().to_dict().items()}
        if "source" in frame.columns
        else {}
    )
    summary = {
        "completed_at": datetime.now(timezone.utc).isoformat(),
        "source": "wyscout_plus_statsbomb_stream",
        "wyscout_matches_imported": int(wyscout_imported),
        "statsbomb": statsbomb,
        "matches": int(frame["match_id"].nunique()),
        "matches_by_source": source_counts,
        "training_rows": int(len(frame)),
        "step_minutes": int(step),
        "direct": direct_metrics,
        "hazard": hazard_metrics,
    }
    (artifacts / "combined_training_summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return summary


def main() -> None:
    parser = argparse.ArgumentParser(description="Train GOOL foundation on Wyscout plus disk-safe streamed StatsBomb")
    parser.add_argument("--work-dir", default="data/combined_foundation")
    parser.add_argument("--step", type=int, default=3)
    parser.add_argument("--statsbomb-limit", type=int, default=None)
    parser.add_argument("--workers", type=int, default=6)
    args = parser.parse_args()
    print(
        json.dumps(
            run(Path(args.work_dir), args.step, args.statsbomb_limit, args.workers),
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
