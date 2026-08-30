from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path

from .archive_statsbomb import import_statsbomb
from .bootstrap_foundation import (
    DYNASTY_DATASET_URL,
    STATSBOMB_OPEN_DATA_URLS,
    _download_zip_with_fallback,
    _extract,
    _find_statsbomb_root,
    _stage_dynasty_archive,
)


def diagnose_statsbomb(work_dir: Path, limit: int | None = 25) -> dict[str, object]:
    raw = work_dir / "raw" / "statsbomb"
    extracted = raw / "extracted"
    archive = work_dir / "raw" / "statsbomb_probe.jsonl"
    raw.mkdir(parents=True, exist_ok=True)
    archive.unlink(missing_ok=True)
    zip_path = raw / "open-data.zip"
    mirror = _download_zip_with_fallback(STATSBOMB_OPEN_DATA_URLS, zip_path)
    _extract(zip_path, extracted)
    root = _find_statsbomb_root(extracted)
    imported = import_statsbomb(root, archive, limit=limit)
    return {
        "source": "statsbomb",
        "status": "ok",
        "mirror": mirror,
        "zip_bytes": int(zip_path.stat().st_size),
        "root": str(root),
        "probe_matches_imported": int(imported),
        "probe_jsonl_bytes": int(archive.stat().st_size if archive.exists() else 0),
    }


def diagnose_dynasty(work_dir: Path) -> dict[str, object]:
    result = _stage_dynasty_archive(work_dir / "raw")
    return {"source": "dynasty", "dataset_url": DYNASTY_DATASET_URL, **result}


def main() -> None:
    parser = argparse.ArgumentParser(description="Diagnose GOOL archive acquisition independently")
    parser.add_argument("source", choices=["statsbomb", "dynasty"])
    parser.add_argument("--work-dir", default="data/archive_diagnostics")
    parser.add_argument("--limit", type=int, default=25)
    args = parser.parse_args()
    work_dir = Path(args.work_dir)
    artifacts = work_dir / "artifacts"
    artifacts.mkdir(parents=True, exist_ok=True)
    try:
        if args.source == "statsbomb":
            result = diagnose_statsbomb(work_dir, args.limit)
        else:
            result = diagnose_dynasty(work_dir)
    except Exception as exc:
        result = {
            "source": args.source,
            "status": "failed",
            "error_type": type(exc).__name__,
            "error": str(exc),
        }
    result["checked_at"] = datetime.now(timezone.utc).isoformat()
    out = artifacts / f"{args.source}_diagnostic.json"
    out.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, indent=2))
    if result.get("status") in {"failed", "unavailable"}:
        raise SystemExit(2)


if __name__ == "__main__":
    main()
