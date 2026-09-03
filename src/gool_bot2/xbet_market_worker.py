from __future__ import annotations

import argparse
import os
import signal
from pathlib import Path

from .xbet_market_robust import RobustXBetMarketCollector
from .xbet_robust_event_guard import install as install_robust_event_guard
from .xbet_score_epoch_guard import install as install_score_epoch_guard
from .xbet_timeline_score_guard import install as install_timeline_score_guard


def main() -> None:
    runtime = Path(os.getenv("RUNTIME_DATA_DIR", "data"))
    parser = argparse.ArgumentParser(description="GOOL 1xBet fast market-pressure collector")
    parser.add_argument("--state", default=os.getenv("XBET_MARKET_STATE", str(runtime / "live" / "xbet_market_state.json")))
    parser.add_argument("--history", default=os.getenv("XBET_MARKET_HISTORY", str(runtime / "live" / "xbet_market_history.jsonl")))
    parser.add_argument("--interval", type=float, default=float(os.getenv("XBET_MARKET_INTERVAL_SECONDS", "12")))
    args = parser.parse_args()
    # Keep the legacy guards installed for compatibility with the base collector,
    # then install the same protections directly on RobustXBetMarketCollector.
    install_score_epoch_guard()
    install_timeline_score_guard()
    install_robust_event_guard()
    collector = RobustXBetMarketCollector(Path(args.state), Path(args.history))
    signal.signal(signal.SIGINT, collector.stop)
    signal.signal(signal.SIGTERM, collector.stop)
    print(
        f"XBET_MARKET started interval={args.interval}s state={args.state} "
        f"collector=merged_roots "
        f"score_epoch_guard={os.getenv('XBET_SCORE_REPRICE_GUARD_SECONDS', '24')}s "
        f"event_reprice_guard={os.getenv('XBET_EVENT_REPRICE_GUARD_SECONDS', '45')}s "
        f"timeline_epoch_guard={os.getenv('XBET_TIMELINE_REPRICE_GUARD_SECONDS', '45')}s "
        f"odds_shock_guard={os.getenv('XBET_ODDS_SHOCK_GUARD_PP', '12')}pp",
        flush=True,
    )
    collector.run(args.interval)


if __name__ == "__main__":
    main()
