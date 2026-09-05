from __future__ import annotations

import argparse
import os
import signal
from pathlib import Path

from .matchbook_exchange import MatchbookExchangeCollector


def main() -> None:
    runtime = Path(os.getenv("RUNTIME_DATA_DIR", "data"))
    parser = argparse.ArgumentParser(description="GOOL Matchbook exchange money-flow collector")
    parser.add_argument(
        "--state",
        default=os.getenv("MATCHBOOK_MARKET_STATE", str(runtime / "live" / "matchbook_market_state.json")),
    )
    parser.add_argument(
        "--interval",
        type=float,
        default=float(os.getenv("MATCHBOOK_MARKET_INTERVAL_SECONDS", "15")),
    )
    args = parser.parse_args()

    collector = MatchbookExchangeCollector(Path(args.state))
    signal.signal(signal.SIGINT, collector.stop)
    signal.signal(signal.SIGTERM, collector.stop)
    print(
        f"MATCHBOOK_EXCHANGE started interval={max(8.0, args.interval):.0f}s "
        f"state={args.state} min_market_volume={os.getenv('MATCHBOOK_MIN_MARKET_VOLUME', '50')}",
        flush=True,
    )
    collector.run(args.interval)


if __name__ == "__main__":
    main()
