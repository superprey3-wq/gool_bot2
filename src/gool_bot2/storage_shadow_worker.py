from __future__ import annotations

from pathlib import Path

from . import shadow_market_worker as worker
from .storage_runtime import PrematchStore, hydrate_prematch


_PREMATCH_STORE = PrematchStore()
_ORIG_PROCESS_RECORD = worker.process_record


def _memory_card(card_dir: Path, record, analysis):
    """Render active experimental cards in memory; do not accumulate PNGs on disk."""
    try:
        return None, worker.render_shadow_market_card(record, analysis)
    except Exception as exc:
        print(f"EXPERIMENT_CARD_ERROR {type(exc).__name__}:{exc}", flush=True)
        return None, None


def _process_record(record, journal_path: Path, analysis_path: Path, card_dir: Path) -> int:
    if hydrate_prematch(record, _PREMATCH_STORE):
        match = record.get("match") or {}
        print(f"EXPERIMENT_PREMATCH_DISK_RESTORE match={match.get('flashscore_event_id','')}", flush=True)
    return _ORIG_PROCESS_RECORD(record, journal_path, analysis_path, card_dir)


worker._save_card = _memory_card
worker.process_record = _process_record


def main() -> None:
    worker.main()


if __name__ == "__main__":
    main()
