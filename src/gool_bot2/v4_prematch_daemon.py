from __future__ import annotations

import os
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

from .prematch_status import update_prematch_status

ROOT = Path("/home/container")
SCRIPT = ROOT / "scripts" / "gool_flashscore_today.py"


def _journal_path() -> Path:
    return Path(
        os.getenv("GOOL_MULTI_JOURNAL_PATH")
        or (Path(os.getenv("RUNTIME_DATA_DIR", "data")) / "live" / "gool_multi_journal.json")
    )


def _deliver_results() -> None:
    from .v4_prematch_delivery import reconcile_and_deliver_prematch_results

    try:
        reconcile_and_deliver_prematch_results(_journal_path())
    except Exception as exc:
        print(f"GOOL_PREMATCH_RESULT_CYCLE_ERROR {type(exc).__name__}:{exc}", flush=True)


def main() -> None:
    discovery_interval = max(1800, int(os.getenv("GOOL_PREMATCH_INTERVAL_SECONDS", "10800")))
    result_interval = max(30, int(os.getenv("GOOL_PREMATCH_RESULT_INTERVAL_SECONDS", "60")))
    cycle_timeout = max(1800, int(os.getenv("GOOL_PREMATCH_CYCLE_TIMEOUT_SECONDS", "7200")))
    os.environ["GOOL_PREMATCH_DELIVER"] = "1"
    # Discovery/pricing may be infrequent, but settlement/result cards must be watched continuously.
    next_discovery = 0.0
    while True:
        now = time.time()
        if now >= next_discovery:
            started = now
            next_discovery = started + discovery_interval
            update_prematch_status(
                running=True,
                last_cycle_started_at=datetime.fromtimestamp(started, timezone.utc).isoformat(),
                next_cycle_at=datetime.fromtimestamp(next_discovery, timezone.utc).isoformat(),
                last_error=None,
            )
            try:
                if not SCRIPT.is_file():
                    raise RuntimeError(f"prematch_script_missing={SCRIPT}")
                proc = subprocess.run([sys.executable, str(SCRIPT)], cwd=str(ROOT), env=os.environ.copy(), timeout=cycle_timeout)
                update_prematch_status(
                    running=False,
                    last_cycle_finished_at=datetime.now(timezone.utc).isoformat(),
                    last_exit_code=int(proc.returncode),
                    last_error=None if proc.returncode == 0 else f"script_exit_{proc.returncode}",
                )
                print(f"GOOL_PREMATCH cycle_exit={proc.returncode}", flush=True)
            except Exception as exc:
                update_prematch_status(
                    running=False,
                    last_cycle_finished_at=datetime.now(timezone.utc).isoformat(),
                    last_exit_code=-1,
                    last_error=f"{type(exc).__name__}:{exc}",
                )
                print(f"GOOL_PREMATCH cycle_error={type(exc).__name__}:{exc}", flush=True)

        _deliver_results()
        time.sleep(float(result_interval))


if __name__ == "__main__":
    main()
