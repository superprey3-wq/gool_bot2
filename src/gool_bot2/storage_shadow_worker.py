from __future__ import annotations

import argparse
import json
import os
import time
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
    parser = argparse.ArgumentParser(description="Active BTTS/team-goal markets with bounded storage")
    runtime = Path(os.getenv("RUNTIME_DATA_DIR", "data"))
    parser.add_argument("--raw-dir", default=os.getenv("RAW_LIVE_DIR", str(runtime / "raw" / "live")))
    parser.add_argument("--journal", default=os.getenv("SHADOW_MARKET_JOURNAL", str(runtime / "live" / "gool_bot2_shadow_markets.json")))
    parser.add_argument("--analysis", default=os.getenv("SHADOW_MARKET_ANALYSIS", str(runtime / "live" / "gool_bot2_shadow_analysis.jsonl")))
    parser.add_argument("--cards", default=os.getenv("SHADOW_MARKET_CARDS", str(runtime / "live" / "shadow_cards")))
    parser.add_argument("--sleep", type=float, default=float(os.getenv("SHADOW_MARKET_SLEEP", "3")))
    args = parser.parse_args()
    raw_dir = Path(args.raw_dir)
    journal = Path(args.journal)
    analysis = Path(args.analysis)
    card_dir = Path(args.cards)
    offsets: dict[str, int] = {}
    bootstrapped = False
    print(f"EXPERIMENT_MARKETS started raw={raw_dir} journal={journal} analysis={analysis} mode=fresh_only", flush=True)
    while True:
        paths = sorted(raw_dir.glob("*.jsonl"))
        if not bootstrapped:
            skipped = 0
            for path in paths:
                try:
                    size = path.stat().st_size
                    offsets[str(path)] = size
                    skipped += size
                except FileNotFoundError:
                    continue
            bootstrapped = True
            print(f"EXPERIMENT_TAIL_BOOTSTRAP files={len(offsets)} skipped_bytes={skipped}", flush=True)
            time.sleep(max(0.5, args.sleep))
            continue
        for path in paths:
            key = str(path)
            try:
                with path.open("r", encoding="utf-8") as fh:
                    fh.seek(offsets.get(key, 0))
                    for line in fh:
                        try:
                            record = json.loads(line)
                        except Exception:
                            continue
                        if isinstance(record, dict):
                            _process_record(record, journal, analysis, card_dir)
                    offsets[key] = fh.tell()
            except FileNotFoundError:
                offsets.pop(key, None)
                continue
        # Drop offsets for raw files removed by retention housekeeping.
        existing = {str(path) for path in paths}
        for key in list(offsets):
            if key not in existing:
                offsets.pop(key, None)
        time.sleep(max(0.5, args.sleep))


if __name__ == "__main__":
    main()
