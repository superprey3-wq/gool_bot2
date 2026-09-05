from __future__ import annotations

import argparse
import os
import signal
from pathlib import Path

from . import matchbook_exchange as exchange
from .matchbook_pagination import fetch_events_paginated


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

    # The original collector intentionally started with one public page. Production
    # must cover the whole football board, so replace only the fetch function while
    # keeping the proven decoder/flow history unchanged.
    exchange._fetch_events = fetch_events_paginated
    collector = exchange.MatchbookExchangeCollector(Path(args.state))
    signal.signal(signal.SIGINT, collector.stop)
    signal.signal(signal.SIGTERM, collector.stop)
    print(
        f"MATCHBOOK_EXCHANGE started interval={max(8.0, args.interval):.0f}s "
        f"state={args.state} min_market_volume={os.getenv('MATCHBOOK_MIN_MARKET_VOLUME', '50')} "
        f"max_pages={os.getenv('MATCHBOOK_MAX_PAGES', '6')}",
        flush=True,
    )
    collector.run(args.interval)


if __name__ == "__main__":
    main()
