from __future__ import annotations

import argparse
import os
import signal
from datetime import datetime
from pathlib import Path
from typing import Any

from .live_collector import LiveSnapshotCollector
from .storage_runtime import PrematchStore, runtime_cleanup


class StorageLiveSnapshotCollector(LiveSnapshotCollector):
    """Live collector with bounded raw storage and persistent one-time PREMATCH cache."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self._prematch_store = PrematchStore()
        self._cleanup_cycles = 0

    def _path_for_now(self, now: datetime) -> Path:
        # Hourly files let us remove stale live data safely without rewriting a file
        # that workers are currently tailing.
        return self.data_dir / now.strftime("%Y-%m-%d-%H.jsonl")

    def _history_for(self, match: Any) -> dict[str, Any]:
        mid = str(match.provider_match_id)
        minimum = int(os.getenv("ANOTHER_GOAL_PREMATCH_MIN_TEAM_MATCHES", "5"))
        cached = self._prematch_context.get(mid) or {}
        if not cached:
            persisted = self._prematch_store.load(mid)
            if persisted and self._history_ready(persisted, minimum):
                self._prematch_context[mid] = persisted
                print(
                    f"PREMATCH_STORE_RESTORE match={mid} home={len(persisted.get('home_recent') or [])} "
                    f"away={len(persisted.get('away_recent') or [])}",
                    flush=True,
                )
                return persisted
        ctx = super()._history_for(match)
        if isinstance(ctx, dict) and ctx:
            self._prematch_store.save(mid, ctx)
        return ctx

    def _live_record(self, match: Any, now: datetime) -> dict[str, Any]:
        record = super()._live_record(match, now)
        mid = str(match.provider_match_id)
        ctx = record.get("prematch_context") or {}
        # PREMATCH rows can be large. Persist once per match and keep only a tiny
        # reference in every minute-by-minute raw snapshot.
        if isinstance(ctx, dict) and ctx and self._prematch_store.save(mid, ctx):
            record.pop("prematch_context", None)
            record["prematch_ref"] = mid
        return record

    def _final_record(self, match_id: str, info: dict[str, Any], state: dict[str, Any], now: datetime) -> dict[str, Any]:
        record = super()._final_record(match_id, info, state, now)
        if not (record.get("prematch_context") or {}):
            cached = self._prematch_store.load(match_id)
            if cached:
                # One final embedded copy guarantees settlement still has context
                # even though the sidecar is removed right after the final row is written.
                record["prematch_context"] = cached
        record["prematch_ref"] = match_id
        return record

    def _append(self, record: dict[str, Any], now: datetime) -> None:
        super()._append(record, now)
        match = record.get("match") or {}
        if bool(match.get("is_finished")):
            mid = str(match.get("flashscore_event_id") or "")
            self._prematch_store.delete(mid)
            print(f"PREMATCH_STORE_CLEAN match={mid} reason=finished", flush=True)

    def collect_once(self) -> dict[str, int]:
        counters = super().collect_once()
        self._cleanup_cycles += 1
        if self._cleanup_cycles >= max(1, int(os.getenv("STORAGE_CLEANUP_EVERY_CYCLES", "5"))):
            self._cleanup_cycles = 0
            result = runtime_cleanup()
            if any(int(v or 0) for v in result.values()):
                print(f"GOOL_STORAGE_RUNTIME {result}", flush=True)
        return counters


def main() -> None:
    parser = argparse.ArgumentParser(description="Collect GOOL live football snapshots with bounded storage")
    parser.add_argument("--data-dir", default=os.getenv("RUNTIME_DATA_DIR", "data") + "/raw/live")
    parser.add_argument("--interval", type=int, default=int(os.getenv("LIVE_INTERVAL_SECONDS", "60")))
    parser.add_argument("--prefilter", type=float, default=float(os.getenv("CORE_ANALYSIS_PREFILTER", "50")))
    parser.add_argument("--secondary-interval", type=int, default=int(os.getenv("SECONDARY_PROVIDER_INTERVAL_MINUTES", "3")))
    args = parser.parse_args()
    collector = StorageLiveSnapshotCollector(
        data_dir=args.data_dir,
        prefilter_threshold=args.prefilter,
        secondary_interval_minutes=args.secondary_interval,
    )
    signal.signal(signal.SIGINT, collector.stop)
    signal.signal(signal.SIGTERM, collector.stop)
    collector.run_forever(interval_seconds=args.interval)


if __name__ == "__main__":
    main()
