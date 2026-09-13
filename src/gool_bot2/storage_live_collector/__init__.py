from __future__ import annotations

import argparse
import importlib.util
import os
import signal
import sys
from pathlib import Path
from typing import Any


# Keep the mature bounded-storage implementation in the legacy module file, but
# shadow it with a package so ``python -m gool_bot2.storage_live_collector`` can
# use full-match detail collection without duplicating the collector body.
_LEGACY_PATH = Path(__file__).resolve().parent.parent / "storage_live_collector.py"
_SPEC = importlib.util.spec_from_file_location(
    "gool_bot2._storage_live_collector_legacy",
    _LEGACY_PATH,
)
if _SPEC is None or _SPEC.loader is None:
    raise RuntimeError(f"storage_live_collector_legacy_missing={_LEGACY_PATH}")
_LEGACY = importlib.util.module_from_spec(_SPEC)
sys.modules[_SPEC.name] = _LEGACY
_SPEC.loader.exec_module(_LEGACY)


class StorageLiveSnapshotCollector(_LEGACY.StorageLiveSnapshotCollector):
    """Production collector with expensive football detail for the whole match.

    Ordinary GOOL is now allowed through 45' in the first half and through 95'
    in the second half.  Those minutes therefore need real Flashscore stats and
    goal-timeline data, not the old cheap market-only snapshot.
    """

    @staticmethod
    def _entry_window(minute: int) -> bool:
        value = int(minute or 0)
        return 1 <= value <= 45 or 46 <= value <= 95

    @staticmethod
    def _eligible_for_detail(minute: int, is_halftime: bool) -> bool:
        return bool(not is_halftime and StorageLiveSnapshotCollector._entry_window(minute))


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Collect GOOL full-match live football snapshots with bounded storage"
    )
    parser.add_argument("--data-dir", default=os.getenv("RUNTIME_DATA_DIR", "data") + "/raw/live")
    parser.add_argument("--interval", type=int, default=int(os.getenv("LIVE_INTERVAL_SECONDS", "60")))
    parser.add_argument("--prefilter", type=float, default=float(os.getenv("CORE_ANALYSIS_PREFILTER", "50")))
    parser.add_argument(
        "--secondary-interval",
        type=int,
        default=int(os.getenv("SECONDARY_PROVIDER_INTERVAL_MINUTES", "3")),
    )
    args = parser.parse_args()
    collector = StorageLiveSnapshotCollector(
        data_dir=args.data_dir,
        prefilter_threshold=args.prefilter,
        secondary_interval_minutes=args.secondary_interval,
    )
    signal.signal(signal.SIGINT, collector.stop)
    signal.signal(signal.SIGTERM, collector.stop)
    print(
        "GOOL_LIVE_FULL_MATCH collector=enabled detail_windows=1-45,46-95 halftime=settlement_only",
        flush=True,
    )
    collector.run_forever(interval_seconds=args.interval)


__all__ = ["StorageLiveSnapshotCollector", "main"]
