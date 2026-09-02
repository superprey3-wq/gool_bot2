from __future__ import annotations

import argparse
import os
import signal
from pathlib import Path

from .xbet_market_pressure import XBetMarketCollector


def main() -> None:
    runtime = Path(os.getenv("RUNTIME_DATA_DIR", "data"))
    parser = argparse.ArgumentParser(description="GOOL 1xBet fast market-pressure collector")
    parser.add_argument("--state", default=os.getenv("XBET_MARKET_STATE", str(runtime / "live" / "xbet_market_state.json")))
    parser.add_argument("--history", default=os.getenv("XBET_MARKET_HISTORY", str(runtime / "live" / "xbet_market_history.jsonl")))
    parser.add_argument("--interval", type=float, default=float(os.getenv("XBET_MARKET_INTERVAL_SECONDS", "12")))
    args = parser.parse_args()
    collector = XBetMarketCollector(Path(args.state), Path(args.history))
    signal.signal(signal.SIGINT, collector.stop)
    signal.signal(signal.SIGTERM, collector.stop)
    print(f"XBET_MARKET started interval={args.interval}s state={args.state}", flush=True)
    collector.run(args.interval)


if __name__ == "__main__":
    main()
