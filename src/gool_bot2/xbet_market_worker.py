from __future__ import annotations

import argparse
import os
import signal
from pathlib import Path

from .xbet_market_pressure import XBetMarketCollector
from .xbet_score_epoch_guard import install as install_score_epoch_guard
from .xbet_timeline_score_guard import install as install_timeline_score_guard


def main() -> None:
    runtime = Path(os.getenv("RUNTIME_DATA_DIR", "data"))
    parser = argparse.ArgumentParser(description="GOOL 1xBet fast market-pressure collector")
    parser.add_argument("--state", default=os.getenv("XBET_MARKET_STATE", str(runtime / "live" / "xbet_market_state.json")))
    parser.add_argument("--history", default=os.getenv("XBET_MARKET_HISTORY", str(runtime / "live" / "xbet_market_history.jsonl")))
    parser.add_argument("--interval", type=float, default=float(os.getenv("XBET_MARKET_INTERVAL_SECONDS", "12")))
    args = parser.parse_args()
    install_score_epoch_guard()
    install_timeline_score_guard()
    collector = XBetMarketCollector(Path(args.state), Path(args.history))
    signal.signal(signal.SIGINT, collector.stop)
    signal.signal(signal.SIGTERM, collector.stop)
    print(
        f"XBET_MARKET started interval={args.interval}s state={args.state} "
        f"score_epoch_guard={os.getenv('XBET_SCORE_REPRICE_GUARD_SECONDS', '24')}s "
        f"timeline_epoch_guard={os.getenv('XBET_TIMELINE_REPRICE_GUARD_SECONDS', '45')}s",
        flush=True,
    )
    collector.run(args.interval)


if __name__ == "__main__":
    main()
