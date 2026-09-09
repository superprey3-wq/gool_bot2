from __future__ import annotations

import argparse
import importlib.util
import os
import signal
import subprocess
import sys
from pathlib import Path


def _ensure_websockets() -> None:
    if importlib.util.find_spec("websockets") is not None:
        return
    print("GOOL_BOOT betdaq installing_dependency=websockets", flush=True)
    subprocess.check_call(
        [sys.executable, "-m", "pip", "install", "--no-cache-dir", "websockets>=15,<16"],
        env={**os.environ, "PIP_NO_CACHE_DIR": "1"},
    )


def main() -> None:
    _ensure_websockets()
    from .betdaq_stream_priority_fix import install_betdaq_stream_priority_fix
    from .betdaq_selection_lifecycle_patch import install_betdaq_selection_lifecycle

    install_betdaq_stream_priority_fix()
    install_betdaq_selection_lifecycle()
    from .betdaq_selection_matched import SelectionMatchedBetdaqCollector

    runtime = Path(os.getenv("RUNTIME_DATA_DIR", "data"))
    parser = argparse.ArgumentParser(description="GOOL BETDAQ anonymous exchange money-flow collector")
    parser.add_argument(
        "--state",
        default=os.getenv("BETDAQ_MARKET_STATE", str(runtime / "live" / "betdaq_market_state.json")),
    )
    parser.add_argument(
        "--interval",
        type=float,
        default=float(os.getenv("BETDAQ_MARKET_INTERVAL_SECONDS", os.getenv("MATCHBOOK_MARKET_INTERVAL_SECONDS", "10"))),
    )
    args = parser.parse_args()
    collector = SelectionMatchedBetdaqCollector(Path(args.state))
    signal.signal(signal.SIGINT, collector.stop)
    signal.signal(signal.SIGTERM, collector.stop)
    print(
        f"BETDAQ_EXCHANGE started mode=anonymous_aapi interval={max(8.0, args.interval):.0f}s "
        f"state={args.state} board=all_today_football markets=match_odds+ft_1h_totals "
        "decoder=live_hardened selection_matched=on result_lifecycle=on",
        flush=True,
    )
    collector.run(args.interval)


if __name__ == "__main__":
    main()
