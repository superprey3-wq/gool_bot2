from __future__ import annotations

import argparse
import json
import os
import signal
import time
from datetime import datetime, timezone
from pathlib import Path

from .live_collector import LiveSnapshotCollector
from .signal_worker import SignalWorker
from .telegram import poll_in_game_callbacks


class LocalRuntime:
    """Single-process collector + inference + Telegram control for small hosts."""

    def __init__(self, data_root: Path, interval_seconds: int = 60) -> None:
        self.data_root = data_root
        self.interval_seconds = max(30, int(interval_seconds))
        self.live_dir = data_root / "raw/live"
        self.journal_path = Path(os.getenv("SIGNAL_JOURNAL_FILE", str(data_root / "gool_bot2_signal_journal.json")))
        self.analysis_path = Path(os.getenv("ANALYSIS_JOURNAL_FILE", str(data_root / "gool_bot2_analysis.jsonl")))
        self.collector = LiveSnapshotCollector(
            data_dir=self.live_dir,
            prefilter_threshold=float(os.getenv("CORE_ANALYSIS_PREFILTER", "50")),
            secondary_interval_minutes=int(os.getenv("SECONDARY_PROVIDER_INTERVAL_MINUTES", "3")),
        )
        self.worker = SignalWorker(
            self.journal_path,
            max_disagreement=float(os.getenv("MODEL_MAX_DISAGREEMENT", "0.20")),
            analysis_path=self.analysis_path,
        )
        self.telegram_offset = 0
        self._stop = False

    def stop(self, *_: object) -> None:
        self._stop = True
        self.collector.stop()

    def _poll_telegram(self) -> int:
        self.telegram_offset, changed = poll_in_game_callbacks(self.journal_path, self.telegram_offset)
        return changed

    def run(self) -> None:
        while not self._stop:
            started = time.monotonic()
            now = datetime.now(timezone.utc)
            try:
                counters = self.collector.collect_once()
                live_file = self.live_dir / f"{now.date().isoformat()}.jsonl"
                emitted = self.worker.process_file(live_file)
                entries = self._poll_telegram()
                print(
                    json.dumps(
                        {
                            "at": datetime.now(timezone.utc).isoformat(),
                            "collector": counters,
                            "signals": emitted,
                            "new_in_game_entries": entries,
                        },
                        ensure_ascii=False,
                    ),
                    flush=True,
                )
            except Exception as exc:
                # Runtime must survive provider/model/Telegram failures.
                print(
                    json.dumps(
                        {"at": datetime.now(timezone.utc).isoformat(), "runtime_error": f"{type(exc).__name__}: {exc}"},
                        ensure_ascii=False,
                    ),
                    flush=True,
                )

            remaining = max(1.0, self.interval_seconds - (time.monotonic() - started))
            end = time.monotonic() + remaining
            while not self._stop and time.monotonic() < end:
                try:
                    self._poll_telegram()
                except Exception:
                    pass
                time.sleep(min(1.0, end - time.monotonic()))


def main() -> None:
    parser = argparse.ArgumentParser(description="Run GOOL 2 collector and local football model in one process")
    parser.add_argument("--data-root", default=os.getenv("RUNTIME_DATA_DIR", "data"))
    parser.add_argument("--interval", type=int, default=int(os.getenv("LIVE_INTERVAL_SECONDS", "60")))
    args = parser.parse_args()

    runtime = LocalRuntime(Path(args.data_root), interval_seconds=args.interval)
    signal.signal(signal.SIGINT, runtime.stop)
    signal.signal(signal.SIGTERM, runtime.stop)
    runtime.run()


if __name__ == "__main__":
    main()
