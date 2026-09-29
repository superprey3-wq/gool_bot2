from __future__ import annotations

import os
import signal
import time
from pathlib import Path

_STOP=False

def _stop(*_: object) -> None:
    global _STOP
    _STOP=True

def _journal_path() -> Path:
    raw=os.getenv("GOOL_MULTI_JOURNAL_PATH","").strip()
    if raw:
        return Path(raw)
    return Path(os.getenv("RUNTIME_DATA_DIR","data"))/"live"/"gool_multi_journal.json"

def main() -> None:
    from .v4_prematch_delivery import reconcile_and_deliver_prematch_results
    signal.signal(signal.SIGINT,_stop)
    signal.signal(signal.SIGTERM,_stop)
    interval=max(30,int(os.getenv("GOOL_PREMATCH_RESULT_INTERVAL_SECONDS","60")))
    journal=_journal_path()
    print(f"GOOL_PREMATCH_RESULT_DAEMON started journal={journal} interval={interval}",flush=True)
    while not _STOP:
        try:
            result=reconcile_and_deliver_prematch_results(journal)
            if (result or {}).get("settled") or (result or {}).get("delivered"):
                print(f"GOOL_PREMATCH_RESULT_DAEMON cycle={result}",flush=True)
        except Exception as exc:
            print(f"GOOL_PREMATCH_RESULT_DAEMON_ERROR {type(exc).__name__}:{exc}",flush=True)
        end=time.monotonic()+interval
        while not _STOP and time.monotonic()<end:
            time.sleep(min(1.0,end-time.monotonic()))

if __name__=="__main__":
    main()
