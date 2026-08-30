from __future__ import annotations

import argparse
import json
import shutil
import urllib.error
import urllib.request
import zipfile
from datetime import datetime, timezone
from pathlib import Path

from .archive_dataset import build_dataframe
from .archive_statsbomb import import_statsbomb
from .archive_wyscout import import_wyscout
from .train_archive_models import _jsonable_metrics, train_archive_models
from .train_hazard_models import train_hazard_models


WYSCOUT_EVENTS_URL = "https://raw.githubusercontent.com/koenvo/wyscout-soccer-match-event-dataset/main/raw_data/events.zip"
WYSCOUT_MATCHES_URL = "https://raw.githubusercontent.com/koenvo/wyscout-soccer-match-event-dataset/main/raw_data/matches.zip"
STATSBOMB_OPEN_DATA_URLS = (
    "https://codeload.github.com/hudl/open-data/zip/refs/heads/master",
    "https://codeload.github.com/statsbomb/open-data/zip/refs/heads/master",
    "https://codeload.github.com/zagilbert/StatsBomb-open-data/zip/refs/heads/master",
)
DYNASTY_DATASET_URL = "https://raw.githubusercontent.com/Afriskaut/dynasty-scouting-league-2024-open-data/main/Datasets.zip"


def _download(url: str, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists() and path.stat().st_size > 0:
        return
    partial = path.with_suffix(path.suffix + ".part")
    partial.unlink(missing_ok=True)
    request = urllib.request.Request(url, headers={"User-Agent": "gool_bot2-foundation/1.2"})
    try:
        with urllib.request.urlopen(request, timeout=600) as response, partial.open("wb") as handle:
            shutil.copyfileobj(response, handle, length=1024 * 1024)
        partial.replace(path)
    except Exception:
        partial.unlink(missing_ok=True)
        raise


def _download_zip_with_fallback(urls: tuple[str, ...], path: Path) -> str:
    if path.exists() and zipfile.is_zipfile(path):
        return "cached"
    path.unlink(missing_ok=True)
    errors: list[str] = []
    for url in urls:
        try:
            _download(url, path)
            if not zipfile.is_zipfile(path):
                raise RuntimeError("downloaded file is not a valid zip archive")
            return url
        except Exception as exc:
            path.unlink(missing_ok=True)
            errors.append(f"{url}: {type(exc).__name__}: {exc}")
    raise RuntimeError("All StatsBomb GitHub mirrors failed: " + " | ".join(errors))


def _extract(path: Path, output: Path) -> None:
    output.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(path) as archive:
        archive.extractall(output)


def _find_statsbomb_root(extracted: Path) -> Path:
    if (extracted / "data" / "matches").exists() and (extracted / "data" / "events").exists():
        return extracted
    for candidate in extracted.iterdir():
        if candidate.is_dir() and (candidate / "data" / "matches").exists() and (candidate / "data" / "events").exists():
            return candidate
    for matches_dir in extracted.rglob("matches"):
        candidate = matches_dir.parent.parent
        if (candidate / "data" / "matches").exists() and (candidate / "data" / "events").exists():
            return candidate
    raise RuntimeError("StatsBomb archive extracted, but data/matches and data/events were not found")


def _stage_dynasty_archive(raw_root: Path) -> dict[str, object]:
    """Download a second independent GitHub event archive for the next importer.

    Afriskaut's Dynasty Scouting League archive is Apache-2.0, compact (~9 MB),
    timestamped JSONL event data with match metadata and pitch coordinates.  We
    stage it now so a StatsBomb network/mirror outage cannot block collecting a
    second source, but we do not silently mix it into model training until its
    event taxonomy is mapped and tested in a dedicated canonical importer.
    """
    dynasty_raw = raw_root / "dynasty"
    dynasty_zip = dynasty_raw / "Datasets.zip"
    dynasty_extracted = dynasty_raw / "extracted"
    try:
        _download(DYNASTY_DATASET_URL, dynasty_zip)
        if not zipfile.is_zipfile(dynasty_zip):
            raise RuntimeError("Dynasty Datasets.zip is invalid")
        _extract(dynasty_zip, dynasty_extracted)
        match_dirs = sum(1 for p in dynasty_extracted.rglob("events.jsonl") if p.is_file())
        return {
            "status": "staged",
            "matches_with_events": int(match_dirs),
            "path": str(dynasty_extracted),
            "source_url": DYNASTY_DATASET_URL,
        }
    except Exception as exc:
        return {
            "status": "unavailable",
            "error": f"{type(exc).__name__}: {exc}",
            "source_url": DYNASTY_DATASET_URL,
        }


def _save_pickle(bundle: object, path: Path) -> None:
    import pickle

    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("wb") as handle:
        pickle.dump(bundle, handle, protocol=pickle.HIGHEST_PROTOCOL)


def run(work_dir: Path, step: int = 2, limit: int | None = None) -> dict[str, object]:
    raw_root = work_dir / "raw"
    wyscout_raw = raw_root / "wyscout"
    wyscout_extracted = wyscout_raw / "extracted"
    statsbomb_raw = raw_root / "statsbomb"
    statsbomb_extracted = statsbomb_raw / "extracted"
    archive_jsonl = raw_root / "open_event_archive.jsonl"
    processed = work_dir / "processed" / "archive_training.csv"
    models = work_dir / "models"
    artifacts = work_dir / "artifacts"

    wyscout_events_zip = wyscout_raw / "events.zip"
    wyscout_matches_zip = wyscout_raw / "matches.zip"
    _download(WYSCOUT_EVENTS_URL, wyscout_events_zip)
    _download(WYSCOUT_MATCHES_URL, wyscout_matches_zip)
    _extract(wyscout_events_zip, wyscout_extracted)
    _extract(wyscout_matches_zip, wyscout_extracted)

    # Stage another independent open GitHub archive regardless of StatsBomb.
    dynasty_status = _stage_dynasty_archive(raw_root)

    statsbomb_zip = statsbomb_raw / "open-data.zip"
    statsbomb_mirror = _download_zip_with_fallback(STATSBOMB_OPEN_DATA_URLS, statsbomb_zip)
    _extract(statsbomb_zip, statsbomb_extracted)
    statsbomb_root = _find_statsbomb_root(statsbomb_extracted)

    wyscout_imported = import_wyscout(wyscout_extracted, archive_jsonl, limit=limit)
    statsbomb_imported = import_statsbomb(statsbomb_root, archive_jsonl, limit=limit)

    frame = build_dataframe(archive_jsonl, cutoffs=range(1, 91, max(1, int(step))))
    if frame.empty:
        raise RuntimeError("Open event archives produced no training rows")
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

    source_counts = {
        str(source): int(count)
        for source, count in frame.groupby("source")["match_id"].nunique().to_dict().items()
    } if "source" in frame.columns else {}

    summary = {
        "completed_at": datetime.now(timezone.utc).isoformat(),
        "source": "wyscout_plus_statsbomb_open_event_archives",
        "new_matches_imported": {
            "wyscout": int(wyscout_imported),
            "statsbomb": int(statsbomb_imported),
        },
        "statsbomb_mirror_used": statsbomb_mirror,
        "auxiliary_archives": {"dynasty_scouting_league_2024": dynasty_status},
        "matches": int(frame["match_id"].nunique()),
        "matches_by_source": source_counts,
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
    parser = argparse.ArgumentParser(description="Download Wyscout + StatsBomb open events and train GOOL foundation models")
    parser.add_argument("--work-dir", default="data/foundation_bootstrap")
    parser.add_argument("--step", type=int, default=2)
    parser.add_argument("--limit", type=int, default=None)
    args = parser.parse_args()
    summary = run(Path(args.work_dir), step=args.step, limit=args.limit)
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
