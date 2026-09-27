from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path("/home/container")
SCRIPT = ROOT / "scripts" / "gool_flashscore_today.py"

def main() -> None:
    interval = max(1800, int(os.getenv("GOOL_PREMATCH_INTERVAL_SECONDS", "10800")))
    os.environ["GOOL_PREMATCH_DELIVER"] = "1"\n    # The persistent Telegram menu is owned by the main worker; prematch cards\n    # use inline controls only and must never replace it.
    while True:
        started = time.time()
        try:
            if not SCRIPT.is_file():
                raise RuntimeError(f"prematch_script_missing={SCRIPT}")
            proc = subprocess.run([sys.executable, str(SCRIPT)], cwd=str(ROOT), env=os.environ.copy(), timeout=1800)
            print(f"GOOL_PREMATCH cycle_exit={proc.returncode}", flush=True)
        except Exception as exc:
            print(f"GOOL_PREMATCH cycle_error={type(exc).__name__}:{exc}", flush=True)
        elapsed = time.time() - started
        time.sleep(max(60.0, interval - elapsed))

if __name__ == "__main__":
    main()
