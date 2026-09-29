from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path

from .v4_prematch_delivery import drain_prematch_result_notifications
from .v4_prematch_settlement import reconcile_pending_prematch

ROOT = Path("/home/container")
SCRIPT = ROOT / "scripts" / "gool_flashscore_today.py"


def _journal_path() -> Path:
    runtime = Path(os.getenv("RUNTIME_DATA_DIR", "data"))
    raw = os.getenv("GOOL_MULTI_JOURNAL_PATH", "").strip()
    return Path(raw) if raw else runtime / "live" / "gool_multi_journal.json"


def _monitor_results(journal_path: Path) -> None:
    try:
        changed = reconcile_pending_prematch(journal_path)
        sent = drain_prematch_result_notifications(journal_path)
        if changed or sent:
            print(
                f"GOOL_PREMATCH_RESULT_MONITOR settled={changed} sent={sent}",
                flush=True,
            )
    except Exception as exc:
        print(f"GOOL_PREMATCH_RESULT_MONITOR_ERROR {type(exc).__name__}:{exc}", flush=True)


def main() -> None:
    scan_interval = max(1800, int(os.getenv("GOOL_PREMATCH_INTERVAL_SECONDS", "10800")))
    result_interval = max(30, int(os.getenv("GOOL_PREMATCH_RESULT_INTERVAL_SECONDS", "60")))
    os.environ["GOOL_PREMATCH_DELIVER"] = "1"
    journal_path = _journal_path()

    proc: subprocess.Popen | None = None
    proc_started = 0.0
    next_scan = 0.0

    # PREMATCH selection is intentionally infrequent, but result settlement and
    # Telegram confirmation are monitored independently every ~60 seconds.
    while True:
        now = time.monotonic()

        if proc is not None:
            code = proc.poll()
            if code is not None:
                print(f"GOOL_PREMATCH cycle_exit={code}", flush=True)
                proc = None
                next_scan = now + scan_interval
            elif now - proc_started > 1800:
                proc.kill()
                print("GOOL_PREMATCH cycle_error=TimeoutExpired:1800s", flush=True)
                proc = None
                next_scan = now + scan_interval

        if proc is None and now >= next_scan:
            try:
                if not SCRIPT.is_file():
                    raise RuntimeError(f"prematch_script_missing={SCRIPT}")
                proc = subprocess.Popen(
                    [sys.executable, str(SCRIPT)],
                    cwd=str(ROOT),
                    env=os.environ.copy(),
                )
                proc_started = now
                print("GOOL_PREMATCH cycle_started=1", flush=True)
            except Exception as exc:
                print(f"GOOL_PREMATCH cycle_error={type(exc).__name__}:{exc}", flush=True)
                next_scan = now + scan_interval

        _monitor_results(journal_path)
        time.sleep(result_interval)


if __name__ == "__main__":
    main()
