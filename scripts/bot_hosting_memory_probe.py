"""Dependency and startup RSS profiler for lightweight GOOL experimentation.

Diagnostics only. Does not run collectors, send Telegram messages, or access bookmaker feeds.
Linux maxrss is reported in KiB. Startup numbers are not steady-state runtime usage.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REPORT = ROOT / "reports" / "bot_hosting_lite" / "import_memory.json"
MODULES = [
    "gool_bot2",
    "gool_bot2.prematch_strategy_scenarios",
    "gool_bot2.hockey_brain_v2",
    "gool_bot2.basketball_brain_v2",
    "gool_bot2.xbet_multisport_steam",
    "gool_bot2.storage_market_signal_worker_var",
]
CHILD = r"""
import importlib
import json
import os
import resource
import sys
import time
start = time.perf_counter()
importlib.import_module(sys.argv[1])
print("GOOL_IMPORT_METRIC " + json.dumps({
  "module": sys.argv[1],
  "rss_kib": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,
  "elapsed_seconds": round(time.perf_counter() - start, 3),
  "loaded_modules": len(sys.modules),
}))
"""

def main() -> None:
    results = []
    for mode in ("full", "no_football_hooks"):
        for module in MODULES:
            env = os.environ.copy()
            env["PYTHONPATH"] = str(ROOT / "src")
            env["GOOL_FOOTBALL_AUTOINSTALL"] = "0" if mode == "no_football_hooks" else "1"
            try:
                proc = subprocess.run(
                    [sys.executable, "-c", CHILD, module],
                    cwd=ROOT, env=env, capture_output=True, text=True, timeout=90,
                )
                row = {"mode": mode, "module": module, "exit_code": proc.returncode}
                marker = next(
                    (line.split("GOOL_IMPORT_METRIC ", 1)[1]
                     for line in proc.stdout.splitlines()
                     if line.startswith("GOOL_IMPORT_METRIC ")),
                    None,
                )
                if marker:
                    row.update(json.loads(marker))
                if proc.returncode:
                    row["error"] = proc.stderr[-1400:]
            except subprocess.TimeoutExpired:
                row = {"mode": mode, "module": module, "error": "timeout_90s"}
            results.append(row)
            print(json.dumps(row, ensure_ascii=False), flush=True)

    REPORT.parent.mkdir(parents=True, exist_ok=True)
    REPORT.write_text(json.dumps({
        "measured_at_utc": datetime.now(timezone.utc).isoformat(),
        "note": "Peak RSS during imports only; live collectors and databases are not started.",
        "results": results,
    }, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print("report=" + str(REPORT.relative_to(ROOT)), flush=True)

if __name__ == "__main__":
    main()
