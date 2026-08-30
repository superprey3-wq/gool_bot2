from __future__ import annotations

import argparse
import json
from pathlib import Path

from .archive_dataset import build_dataframe
from .archive_football_data import import_football_data
from .archive_statsbomb import import_statsbomb
from .archive_wyscout import import_wyscout
from .bootstrap_foundation import (
    STATSBOMB_OPEN_DATA_URLS,
    WYSCOUT_EVENTS_URL,
    WYSCOUT_MATCHES_URL,
    _download,
    _download_zip_with_fallback,
    _extract,
    _find_statsbomb_root,
    _stage_dynasty_archive,
)


def _error_status(exc: Exception) -> dict[str, object]:
    return {"status": "unavailable", "error": f"{type(exc).__name__}: {exc}"}


def run(work_dir: Path, step: int = 2, limit: int | None = None) -> dict[str, object]:
    raw_root = work_dir / "raw"
    wyscout_raw = raw_root / "wyscout"
    wyscout_extracted = wyscout_raw / "extracted"
    statsbomb_raw = raw_root / "statsbomb"
    statsbomb_extracted = statsbomb_raw / "extracted"
    archive_jsonl = raw_root / "open_event_archive.jsonl"
    processed = work_dir / "processed" / "archive_training.csv"
    football_data_csv = work_dir / "processed" / "football_data_htft.csv"
    artifacts = work_dir / "artifacts"

    artifacts.mkdir(parents=True, exist_ok=True)
    archive_jsonl.unlink(missing_ok=True)

    source_status: dict[str, dict[str, object]] = {}
    wyscout_imported = 0
    statsbomb_imported = 0
    statsbomb_mirror: str | None = None

    # Prepare each event source independently. A temporary outage in one public
    # archive must not prevent the other source from reaching model training.
    try:
        wyscout_events_zip = wyscout_raw / "events.zip"
        wyscout_matches_zip = wyscout_raw / "matches.zip"
        _download(WYSCOUT_EVENTS_URL, wyscout_events_zip)
        _download(WYSCOUT_MATCHES_URL, wyscout_matches_zip)
        _extract(wyscout_events_zip, wyscout_extracted)
        _extract(wyscout_matches_zip, wyscout_extracted)
        wyscout_imported = int(import_wyscout(wyscout_extracted, archive_jsonl, limit=limit))
        source_status["wyscout"] = {"status": "prepared", "matches_imported": wyscout_imported}
    except Exception as exc:
        source_status["wyscout"] = _error_status(exc)

    dynasty_status = _stage_dynasty_archive(raw_root)

    try:
        statsbomb_zip = statsbomb_raw / "open-data.zip"
        statsbomb_mirror = _download_zip_with_fallback(STATSBOMB_OPEN_DATA_URLS, statsbomb_zip)
        _extract(statsbomb_zip, statsbomb_extracted)
        statsbomb_root = _find_statsbomb_root(statsbomb_extracted)
        statsbomb_imported = int(import_statsbomb(statsbomb_root, archive_jsonl, limit=limit))
        source_status["statsbomb"] = {
            "status": "prepared",
            "matches_imported": statsbomb_imported,
            "mirror": statsbomb_mirror,
        }
    except Exception as exc:
        source_status["statsbomb"] = _error_status(exc)

    # Always persist source diagnostics before building the dataframe so a
    # failed run leaves a useful artifact instead of an opaque red circle.
    diagnostic = {
        "phase": "prepare_sources",
        "event_sources": source_status,
        "statsbomb_mirror_used": statsbomb_mirror,
        "auxiliary_archives": {"dynasty_scouting_league_2024": dynasty_status},
    }
    (artifacts / "foundation_source_status.json").write_text(
        json.dumps(diagnostic, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    if not archive_jsonl.exists() or archive_jsonl.stat().st_size == 0:
        failure = {
            **diagnostic,
            "status": "failed",
            "reason": "No usable Wyscout or StatsBomb event rows were imported",
        }
        (artifacts / "foundation_prepare_summary.json").write_text(
            json.dumps(failure, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        raise RuntimeError(
            "No usable event source was prepared. "
            f"Wyscout={source_status.get('wyscout')}; StatsBomb={source_status.get('statsbomb')}"
        )

    frame = build_dataframe(archive_jsonl, cutoffs=range(1, 91, max(1, int(step))))
    if frame.empty:
        failure = {**diagnostic, "status": "failed", "reason": "Open event archives produced no training rows"}
        (artifacts / "foundation_prepare_summary.json").write_text(
            json.dumps(failure, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        raise RuntimeError("Open event archives produced no training rows")

    processed.parent.mkdir(parents=True, exist_ok=True)
    frame.to_csv(processed, index=False)

    # Football-Data remains optional and separate from the live event heads.
    try:
        football_data_status = import_football_data(football_data_csv)
        football_data_status["status"] = "prepared"
    except Exception as exc:
        football_data_status = {
            "status": "unavailable",
            "error": f"{type(exc).__name__}: {exc}",
            "dataset": str(football_data_csv),
        }

    source_counts = {
        str(source): int(count)
        for source, count in frame.groupby("source")["match_id"].nunique().to_dict().items()
    } if "source" in frame.columns else {}

    summary = {
        "phase": "prepare",
        "status": "prepared",
        "event_sources": source_status,
        "new_matches_imported": {"wyscout": wyscout_imported, "statsbomb": statsbomb_imported},
        "football_data_htft": football_data_status,
        "statsbomb_mirror_used": statsbomb_mirror,
        "auxiliary_archives": {"dynasty_scouting_league_2024": dynasty_status},
        "matches": int(frame["match_id"].nunique()),
        "matches_by_source": source_counts,
        "training_rows": int(len(frame)),
        "step_minutes": int(step),
        "dataset": str(processed),
    }
    (artifacts / "foundation_prepare_summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return summary


def main() -> None:
    parser = argparse.ArgumentParser(description="Prepare Wyscout + StatsBomb + Football-Data archives without training")
    parser.add_argument("--work-dir", default="data/foundation_bootstrap")
    parser.add_argument("--step", type=int, default=2)
    parser.add_argument("--limit", type=int, default=None)
    args = parser.parse_args()
    print(json.dumps(run(Path(args.work_dir), args.step, args.limit), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
