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


_TOP_LEAGUE_MARKERS = (
    "premier league",
    "laliga",
    "la liga",
    "serie a",
    "bundesliga",
    "ligue 1",
    "champions league",
    "europa league",
    "conference league",
)


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
    """Fetch expensive GetGameZip only where GOOL can use it.

    Ordinary GOOL writes a short-lived demand after football analysis reaches
    PASS/BORDERLINE. Detailed 1xBet markets are then fetched for every demanded
    match. Autonomous STEAM keeps an independent narrow watch lane: all active
    top-league matches plus a rotating sample of the remaining active matches.
    This preserves market-only anomaly detection without polling hundreds of
    irrelevant LIVE games every 12 seconds.
    """

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self._steam_cursor = 0

    @staticmethod
    def _entry_window(match: Any) -> bool:
        try:
            minute = int(match.minute or 0)
        except (TypeError, ValueError):
            return False
        return 1 <= minute <= 35 or 46 <= minute <= 75

    @staticmethod
    def _top_league(match: Any) -> bool:
        text = str(getattr(match, "league", "") or "").casefold()
        return any(marker in text for marker in _TOP_LEAGUE_MARKERS)

    def _select_matches(self, matches: list[Any]) -> tuple[list[Any], dict[str, int]]:
        active = [match for match in matches if self._entry_window(match)]
        demands = load_active_demands()
        demand_ids = set(demands)

        selected: list[Any] = []
        selected_ids: set[str] = set()

        def add(match: Any) -> None:
            match_id = str(match.provider_match_id)
            if match_id not in selected_ids:
                selected.append(match)
                selected_ids.add(match_id)

        demanded = [match for match in active if str(match.provider_match_id) in demand_ids]
        for match in demanded:
            add(match)

        # STEAM is the deliberate exception to football-first demand. Keep all
        # top competitions in the watch lane so premium games are never missed.
        top_watch = [match for match in active if self._top_league(match)]
        for match in top_watch:
            add(match)

        remaining = [match for match in active if str(match.provider_match_id) not in selected_ids]
        try:
            watch_cap = max(0, min(96, int(os.getenv("XBET_STEAM_WATCH_PER_CYCLE", "16"))))
        except (TypeError, ValueError):
            watch_cap = 16
        rotated: list[Any] = []
        if remaining and watch_cap > 0:
            start = self._steam_cursor % len(remaining)
            count = min(watch_cap, len(remaining))
            rotated = [remaining[(start + offset) % len(remaining)] for offset in range(count)]
            self._steam_cursor = (start + count) % len(remaining)
            for match in rotated:
                add(match)

        return selected, {
            "live": len(matches),
            "active_window": len(active),
            "demanded": len(demanded),
            "top_steam_watch": len({str(match.provider_match_id) for match in top_watch}),
            "rotating_steam_watch": len(rotated),
            "selected": len(selected),
        }

    def collect_once(self) -> dict[str, Any]:
        # Existing robust score/VAR/red-card guards are preserved by calling the
        # normal collector with a temporarily narrowed Flashscore live set.
        all_matches = list(self.flashscore.live_matches())
        selected, stats = self._select_matches(all_matches)
        original_live_matches = self.flashscore.live_matches
        self.flashscore.live_matches = lambda: list(selected)  # type: ignore[method-assign]
        try:
            state = super().collect_once()
        finally:
            self.flashscore.live_matches = original_live_matches  # type: ignore[method-assign]
        print(
            "XBET_DEMAND "
            f"live={stats['live']} active={stats['active_window']} demanded={stats['demanded']} "
            f"top_watch={stats['top_steam_watch']} rotating_watch={stats['rotating_steam_watch']} "
            f"selected={stats['selected']} fetched={len((state.get('matches') or {}))}",
            flush=True,
        )
        return state


def main() -> None:
    runtime = Path(os.getenv("RUNTIME_DATA_DIR", "data"))
    parser = argparse.ArgumentParser(description="GOOL 1xBet demand-driven market-pressure collector")
    parser.add_argument("--state", default=os.getenv("XBET_MARKET_STATE", str(runtime / "live" / "xbet_market_state.json")))
    parser.add_argument("--history", default=os.getenv("XBET_MARKET_HISTORY", str(runtime / "live" / "xbet_market_history.jsonl")))
    parser.add_argument("--interval", type=float, default=float(os.getenv("XBET_MARKET_INTERVAL_SECONDS", "12")))
    args = parser.parse_args()
    install_score_epoch_guard()
    install_timeline_score_guard()
    install_robust_event_guard()
    os.environ.setdefault("XBET_GAME_WORKERS", "12")

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
        f"collector=demand_driven_robust workers={os.getenv('XBET_GAME_WORKERS', '12')} "
        f"steam_watch={os.getenv('XBET_STEAM_WATCH_PER_CYCLE', '16')} "
        f"demand_ttl={os.getenv('XBET_MARKET_DEMAND_TTL_SECONDS', '90')}s "
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
