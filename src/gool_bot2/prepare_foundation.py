from __future__ import annotations

import argparse
import json
from pathlib import Path

from .archive_dataset import build_dataframe
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


def _write_status(path: Path, payload: dict[str, object]) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def run(work_dir: Path, step: int = 2, limit: int | None = None) -> dict[str, object]:
    raw_root = work_dir / "raw"
    wyscout_raw = raw_root / "wyscout"
    wyscout_extracted = wyscout_raw / "extracted"
    statsbomb_raw = raw_root / "statsbomb"
    statsbomb_extracted = statsbomb_raw / "extracted"
    archive_jsonl = raw_root / "open_event_archive.jsonl"
    processed = work_dir / "processed" / "archive_training.csv"
    artifacts = work_dir / "artifacts"

    artifacts.mkdir(parents=True, exist_ok=True)
    archive_jsonl.unlink(missing_ok=True)

    source_status: dict[str, dict[str, object]] = {}
    wyscout_imported = 0
    statsbomb_imported = 0
    statsbomb_mirror: str | None = None

    def persist_progress(stage: str) -> None:
        _write_status(
            artifacts / "foundation_source_status.json",
            {
                "phase": "prepare_sources",
                "stage": stage,
                "event_sources": source_status,
                "statsbomb_mirror_used": statsbomb_mirror,
            },
        )

    print("[foundation] starting Wyscout preparation", flush=True)
    persist_progress("wyscout_start")
    try:
        wyscout_events_zip = wyscout_raw / "events.zip"
        wyscout_matches_zip = wyscout_raw / "matches.zip"
        _download(WYSCOUT_EVENTS_URL, wyscout_events_zip)
        print("[foundation] Wyscout events downloaded", flush=True)
        _download(WYSCOUT_MATCHES_URL, wyscout_matches_zip)
        print("[foundation] Wyscout matches downloaded", flush=True)
        _extract(wyscout_events_zip, wyscout_extracted)
        _extract(wyscout_matches_zip, wyscout_extracted)
        print("[foundation] Wyscout archives extracted", flush=True)
        wyscout_imported = int(import_wyscout(wyscout_extracted, archive_jsonl, limit=limit))
        source_status["wyscout"] = {"status": "prepared", "matches_imported": wyscout_imported}
        print(f"[foundation] Wyscout imported matches={wyscout_imported}", flush=True)
    except Exception as exc:
        source_status["wyscout"] = _error_status(exc)
        print(f"[foundation] Wyscout unavailable: {source_status['wyscout']}", flush=True)
    persist_progress("wyscout_done")

    dynasty_status = _stage_dynasty_archive(raw_root)

    print("[foundation] starting StatsBomb preparation", flush=True)
    persist_progress("statsbomb_start")
    try:
        statsbomb_zip = statsbomb_raw / "open-data.zip"
        statsbomb_mirror = _download_zip_with_fallback(STATSBOMB_OPEN_DATA_URLS, statsbomb_zip)
        print(f"[foundation] StatsBomb downloaded mirror={statsbomb_mirror}", flush=True)
        _extract(statsbomb_zip, statsbomb_extracted)
        statsbomb_root = _find_statsbomb_root(statsbomb_extracted)
        print(f"[foundation] StatsBomb extracted root={statsbomb_root}", flush=True)
        statsbomb_imported = int(import_statsbomb(statsbomb_root, archive_jsonl, limit=limit))
        source_status["statsbomb"] = {
            "status": "prepared",
            "matches_imported": statsbomb_imported,
            "mirror": statsbomb_mirror,
        }
        print(f"[foundation] StatsBomb imported matches={statsbomb_imported}", flush=True)
    except Exception as exc:
        source_status["statsbomb"] = _error_status(exc)
        print(f"[foundation] StatsBomb unavailable: {source_status['statsbomb']}", flush=True)
    persist_progress("statsbomb_done")

    diagnostic = {
        "phase": "prepare_sources",
        "stage": "sources_done",
        "event_sources": source_status,
        "statsbomb_mirror_used": statsbomb_mirror,
        "auxiliary_archives": {"dynasty_scouting_league_2024": dynasty_status},
        "football_data_htft": {
            "status": "skipped",
            "reason": "Not needed for live event heads; trained separately in tests workflow",
        },
    }
    _write_status(artifacts / "foundation_source_status.json", diagnostic)

    if not archive_jsonl.exists() or archive_jsonl.stat().st_size == 0:
        failure = {
            **diagnostic,
            "status": "failed",
            "reason": "No usable Wyscout or StatsBomb event rows were imported",
        }
        _write_status(artifacts / "foundation_prepare_summary.json", failure)
        raise RuntimeError(
            "No usable event source was prepared. "
            f"Wyscout={source_status.get('wyscout')}; StatsBomb={source_status.get('statsbomb')}"
        )

    print("[foundation] building minute-level event dataframe", flush=True)
    frame = build_dataframe(archive_jsonl, cutoffs=range(1, 91, max(1, int(step))))
    if frame.empty:
        failure = {**diagnostic, "status": "failed", "reason": "Open event archives produced no training rows"}
        _write_status(artifacts / "foundation_prepare_summary.json", failure)
        raise RuntimeError("Open event archives produced no training rows")

    processed.parent.mkdir(parents=True, exist_ok=True)
    frame.to_csv(processed, index=False)
    print(f"[foundation] event dataset saved rows={len(frame)} matches={frame['match_id'].nunique()}", flush=True)

    source_counts = {
        str(source): int(count)
        for source, count in frame.groupby("source")["match_id"].nunique().to_dict().items()
    } if "source" in frame.columns else {}

    summary = {
        "phase": "prepare",
        "status": "prepared",
        "event_sources": source_status,
        "new_matches_imported": {"wyscout": wyscout_imported, "statsbomb": statsbomb_imported},
        "football_data_htft": diagnostic["football_data_htft"],
        "statsbomb_mirror_used": statsbomb_mirror,
        "auxiliary_archives": {"dynasty_scouting_league_2024": dynasty_status},
        "matches": int(frame["match_id"].nunique()),
        "matches_by_source": source_counts,
        "training_rows": int(len(frame)),
        "step_minutes": int(step),
        "dataset": str(processed),
    }
    _write_status(artifacts / "foundation_prepare_summary.json", summary)
    return summary


def main() -> None:
    parser = argparse.ArgumentParser(description="Prepare Wyscout + StatsBomb event archives without unrelated Football-Data downloads")
    parser.add_argument("--work-dir", default="data/foundation_bootstrap")
    parser.add_argument("--step", type=int, default=2)
    parser.add_argument("--limit", type=int, default=None)
    args = parser.parse_args()
    print(json.dumps(run(Path(args.work_dir), args.step, args.limit), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
