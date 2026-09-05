from __future__ import annotations

import argparse
import os
import signal
import threading
from pathlib import Path
from typing import Any

from .storage_runtime import trim_file_tail
from .xbet_market_demand import load_active_demands
from .xbet_market_robust import RobustXBetMarketCollector
from .xbet_prematch_market import XBetPrematchCollector
from .xbet_robust_event_guard import install as install_robust_event_guard
from .xbet_score_epoch_guard import install as install_score_epoch_guard
from .xbet_timeline_score_guard import install as install_timeline_score_guard


class BoundedRobustXBetMarketCollector(RobustXBetMarketCollector):
    """Robust collector with a hard cap on disposable JSONL market history."""

    def collect_once(self) -> dict[str, Any]:
        state = super().collect_once()
        keep = max(
            1024 * 1024,
            int(os.getenv("XBET_HISTORY_RUNTIME_KEEP_BYTES", str(12 * 1024 * 1024))),
        )
        freed = trim_file_tail(self.history_path, keep)
        if freed:
            print(
                f"XBET_HISTORY_TRIM file={self.history_path.name} freed={freed} keep={keep}",
                flush=True,
            )
        return state


class DemandDrivenXBetMarketCollector(BoundedRobustXBetMarketCollector):
    """Monitor every LIVE match while prioritising an ordinary GOOL Brain demand.

    Ordinary GOOL writes a short-lived demand only after the football Brain
    reaches PASS/BORDERLINE. That demand never excludes another game: it only
    moves the selected match to the front of the same cycle so the Brain gets
    the freshest possible quote. Autonomous 1xBet STEAM continuously watches
    every LIVE match from kickoff until the match is finished, including
    half-time and added time, with no league priority, rotation or minute cap.
    """

    @staticmethod
    def _market_watch_live(match: Any) -> bool:
        try:
            minute = int(match.minute or 0)
        except (TypeError, ValueError):
            return False
        return minute > 0 and not bool(getattr(match, "is_finished", False))

    def _select_matches(self, matches: list[Any]) -> tuple[list[Any], dict[str, int]]:
        live_watch = [match for match in matches if self._market_watch_live(match)]
        demand_ids = set(load_active_demands())

        demanded = [match for match in live_watch if str(match.provider_match_id) in demand_ids]
        background = [match for match in live_watch if str(match.provider_match_id) not in demand_ids]
        selected = [*demanded, *background]

        return selected, {
            "live": len(matches),
            "live_watch": len(live_watch),
            "demanded": len(demanded),
            "background": len(background),
            "selected": len(selected),
        }

    def collect_once(self) -> dict[str, Any]:
        # Existing robust score/VAR/red-card guards are preserved by calling the
        # normal collector with the complete LIVE market-watch set.
        all_matches = list(self.flashscore.live_matches())
        selected, stats = self._select_matches(all_matches)
        original_live_matches = self.flashscore.live_matches
        self.flashscore.live_matches = lambda: list(selected)  # type: ignore[method-assign]
        try:
            state = super().collect_once()
        finally:
            self.flashscore.live_matches = original_live_matches  # type: ignore[method-assign]
        print(
            "XBET_ALL_MARKETS "
            f"live={stats['live']} watched={stats['live_watch']} demanded={stats['demanded']} "
            f"background={stats['background']} selected={stats['selected']} "
            f"fetched={len((state.get('matches') or {}))}",
            flush=True,
        )
        return state


def main() -> None:
    runtime = Path(os.getenv("RUNTIME_DATA_DIR", "data"))
    parser = argparse.ArgumentParser(description="GOOL 1xBet all-LIVE market-pressure collector")
    parser.add_argument("--state", default=os.getenv("XBET_MARKET_STATE", str(runtime / "live" / "xbet_market_state.json")))
    parser.add_argument("--history", default=os.getenv("XBET_MARKET_HISTORY", str(runtime / "live" / "xbet_market_history.jsonl")))
    parser.add_argument("--interval", type=float, default=float(os.getenv("XBET_MARKET_INTERVAL_SECONDS", "12")))
    args = parser.parse_args()
    install_score_epoch_guard()
    install_timeline_score_guard()
    install_robust_event_guard()
    os.environ.setdefault("XBET_GAME_WORKERS", "24")

    collector = DemandDrivenXBetMarketCollector(Path(args.state), Path(args.history))
    prematch_state = Path(os.getenv("XBET_PREMATCH_STATE", str(runtime / "live" / "xbet_prematch_market.json")))
    prematch_interval = max(60.0, float(os.getenv("XBET_PREMATCH_INTERVAL_SECONDS", "300")))
    prematch = XBetPrematchCollector(prematch_state)
    prematch_thread = threading.Thread(
        target=prematch.run,
        args=(prematch_interval,),
        name="xbet-prematch",
        daemon=True,
    )

    def stop_all(*_: object) -> None:
        prematch.stop()
        collector.stop()

    signal.signal(signal.SIGINT, stop_all)
    signal.signal(signal.SIGTERM, stop_all)
    prematch_thread.start()
    print(
        f"XBET_MARKET started interval={args.interval}s state={args.state} "
        f"collector=all_live_robust workers={os.getenv('XBET_GAME_WORKERS', '24')} "
        f"demand_priority=on demand_ttl={os.getenv('XBET_MARKET_DEMAND_TTL_SECONDS', '90')}s "
        f"history_keep={os.getenv('XBET_HISTORY_RUNTIME_KEEP_BYTES', str(12 * 1024 * 1024))} "
        f"score_epoch_guard={os.getenv('XBET_SCORE_REPRICE_GUARD_SECONDS', '24')}s "
        f"event_reprice_guard={os.getenv('XBET_EVENT_REPRICE_GUARD_SECONDS', '45')}s "
        f"timeline_epoch_guard={os.getenv('XBET_TIMELINE_REPRICE_GUARD_SECONDS', '45')}s "
        f"odds_shock_guard={os.getenv('XBET_ODDS_SHOCK_GUARD_PP', '12')}pp "
        f"prematch_state={prematch_state} prematch_interval={prematch_interval:.0f}s",
        flush=True,
    )
    collector.run(args.interval)
    prematch.stop()


if __name__ == "__main__":
    main()
