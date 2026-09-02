from __future__ import annotations

from pathlib import Path

from . import signal_worker_all as base
from . import signal_worker_all_cards as cards
from .storage_runtime import PrematchStore, hydrate_prematch


class StorageCardAllMatchSignalWorker(cards.CardAllMatchSignalWorker):
    """Card worker that restores PREMATCH from the persistent sidecar."""

    def __init__(self, journal_path: Path, max_disagreement: float = 0.20, analysis_path: Path | None = None) -> None:
        super().__init__(journal_path, max_disagreement=max_disagreement, analysis_path=analysis_path)
        self._prematch_store_disk = PrematchStore()

    def _process(self, record):
        if hydrate_prematch(record, self._prematch_store_disk):
            match = record.get("match") or {}
            mid = str(match.get("flashscore_event_id") or "")
            print(f"PREMATCH_DISK_RESTORE match={mid}", flush=True)
        emitted = super()._process(record)
        match = record.get("match") or {}
        if bool(match.get("is_finished")):
            mid = str(match.get("flashscore_event_id") or "")
            self._live_history.pop(mid, None)
            self._live_epoch_start.pop(mid, None)
            self._prematch_ready_cache.pop(mid, None)
        return emitted


# signal_worker_all_cards patches all active strategy functions at import time.
# Swap only the class that base.main() instantiates, keeping menu polling,
# settlement, cards and Telegram behaviour unchanged.
base.AllMatchSignalWorker = StorageCardAllMatchSignalWorker


def main() -> None:
    base.main()


if __name__ == "__main__":
    main()
