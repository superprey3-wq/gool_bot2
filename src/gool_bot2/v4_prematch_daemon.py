from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path

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
    os.environ["GOOL_PREMATCH_DELIVER"] = "1"
    # Discovery/pricing may be infrequent, but settlement/result cards must be watched continuously.
    next_discovery = 0.0
    while True:
        now = time.time()
        if now >= next_discovery:
            started = now
            try:
                if not SCRIPT.is_file():
                    raise RuntimeError(f"prematch_script_missing={SCRIPT}")
                proc = subprocess.run([sys.executable, str(SCRIPT)], cwd=str(ROOT), env=os.environ.copy(), timeout=1800)
                print(f"GOOL_PREMATCH cycle_exit={proc.returncode}", flush=True)
            except Exception as exc:
                print(f"GOOL_PREMATCH cycle_error={type(exc).__name__}:{exc}", flush=True)
            next_discovery = started + discovery_interval

        _deliver_results()
        time.sleep(float(result_interval))


if __name__ == "__main__":
    main()
