from __future__ import annotations

import argparse
import json
import os
import signal
import time
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .prefilter import football_prefilter
from .providers.flashscore import FlashscoreProvider
from .providers.fotmob import FotMobProvider
from .providers.scores365 import Scores365Provider


class LiveSnapshotCollector:
    """Append-only football snapshot collector designed for a small server.

    The Flashscore master feed discovers every live match. Detailed Flashscore
    stats are fetched only inside useful GOOL windows. FotMob/365 enrichment is
    fetched only for deterministic candidates, keeping request volume bounded.
    """

    def __init__(
        self,
        data_dir: str | Path = "data/raw/live",
        prefilter_threshold: float = 50.0,
        secondary_interval_minutes: int = 3,
    ) -> None:
        self.data_dir = Path(data_dir)
        self.prefilter_threshold = float(prefilter_threshold)
        self.secondary_interval_minutes = max(1, int(secondary_interval_minutes))
        self.flashscore = FlashscoreProvider()
        self.fotmob = FotMobProvider()
        self.scores365 = Scores365Provider()
        self._last_secondary_minute: dict[str, int] = {}
        self._stop = False

    @staticmethod
    def _eligible_for_detail(minute: int, is_halftime: bool) -> bool:
        if is_halftime:
            return True
        # Covers FH goal, another-goal, HT/early-2H and re-entry windows while
        # avoiding detail requests during the first warm-up minutes and late FT.
        return 10 <= minute <= 80

    def _secondary_due(self, match_id: str, minute: int) -> bool:
        previous = self._last_secondary_minute.get(match_id)
        if previous is None or minute - previous >= self.secondary_interval_minutes:
            self._last_secondary_minute[match_id] = minute
            return True
        return False

    def _path_for_now(self, now: datetime) -> Path:
        return self.data_dir / f"{now.date().isoformat()}.jsonl"

    def _append(self, record: dict[str, Any], now: datetime) -> None:
        path = self._path_for_now(now)
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, ensure_ascii=False, separators=(",", ":")) + "\n")

    def collect_once(self) -> dict[str, int]:
        now = datetime.now(timezone.utc)
        matches = self.flashscore.live_matches()
        counters = {"live": len(matches), "detail": 0, "candidate": 0, "secondary": 0, "errors": 0}

        for match in matches:
            try:
                minute = int(match.minute or 0)
                record: dict[str, Any] = {
                    "schema_version": 1,
                    "captured_at": now.isoformat(),
                    "source_observed_at": now.isoformat(),
                    "ingested_at": datetime.now(timezone.utc).isoformat(),
                    "match": {
                        "flashscore_event_id": match.provider_match_id,
                        "home": match.home,
                        "away": match.away,
                        "league": match.league,
                        "minute": minute,
                        "home_score": match.home_score,
                        "away_score": match.away_score,
                        "is_halftime": match.is_halftime,
                        "status_code": match.meta.get("status_code", ""),
                    },
                    "providers": {"flashscore": {"id": match.provider_match_id, "stats": {}, "meta": match.meta}},
                    "prefilter": {"score": 0.0, "candidate": False, "reasons": []},
                }

                if self._eligible_for_detail(minute, match.is_halftime):
                    fs_stats = self.flashscore.fetch_stats(match.provider_match_id)
                    goals = self.flashscore.fetch_goal_timeline(match.provider_match_id)
                    counters["detail"] += 1
                    record["providers"]["flashscore"] = {
                        "id": match.provider_match_id,
                        "stats": fs_stats,
                        "meta": {**match.meta, "goal_timeline": goals},
                    }
                    pref = football_prefilter(fs_stats, minute, threshold=self.prefilter_threshold)
                    record["prefilter"] = {
                        "score": pref.score,
                        "candidate": pref.candidate,
                        "reasons": list(pref.reasons),
                    }

                    if pref.candidate:
                        counters["candidate"] += 1
                        if self._secondary_due(match.provider_match_id, minute):
                            fm = self.fotmob.enrich(match.home, match.away)
                            sc = self.scores365.enrich(match.home, match.away)
                            if fm:
                                record["providers"][fm.provider] = {
                                    "id": fm.provider_match_id,
                                    "stats": fm.stats,
                                    "meta": fm.meta,
                                }
                            if sc:
                                record["providers"][sc.provider] = {
                                    "id": sc.provider_match_id,
                                    "stats": sc.stats,
                                    "meta": sc.meta,
                                }
                            counters["secondary"] += int(bool(fm)) + int(bool(sc))

                self._append(record, now)
            except Exception as exc:  # collector must not die because one match/provider failed
                counters["errors"] += 1
                error_record = {
                    "schema_version": 1,
                    "captured_at": now.isoformat(),
                    "match": {"flashscore_event_id": match.provider_match_id, "home": match.home, "away": match.away},
                    "collector_error": f"{type(exc).__name__}: {exc}",
                }
                self._append(error_record, now)
        return counters

    def stop(self, *_: object) -> None:
        self._stop = True

    def run_forever(self, interval_seconds: int = 60) -> None:
        interval_seconds = max(30, int(interval_seconds))
        while not self._stop:
            started = time.monotonic()
            try:
                counters = self.collect_once()
                print(json.dumps({"collector": counters, "at": datetime.now(timezone.utc).isoformat()}), flush=True)
            except Exception as exc:
                # Top-level protection: network/provider failures cannot terminate the daemon.
                print(json.dumps({"collector_error": f"{type(exc).__name__}: {exc}"}), flush=True)
            elapsed = time.monotonic() - started
            remaining = max(1.0, interval_seconds - elapsed)
            end = time.monotonic() + remaining
            while not self._stop and time.monotonic() < end:
                time.sleep(min(1.0, end - time.monotonic()))


def main() -> None:
    parser = argparse.ArgumentParser(description="Collect GOOL live football snapshots")
    parser.add_argument("--data-dir", default=os.getenv("RUNTIME_DATA_DIR", "data") + "/raw/live")
    parser.add_argument("--interval", type=int, default=int(os.getenv("LIVE_INTERVAL_SECONDS", "60")))
    parser.add_argument("--prefilter", type=float, default=float(os.getenv("CORE_ANALYSIS_PREFILTER", "50")))
    parser.add_argument("--secondary-interval", type=int, default=int(os.getenv("SECONDARY_PROVIDER_INTERVAL_MINUTES", "3")))
    args = parser.parse_args()

    collector = LiveSnapshotCollector(
        data_dir=args.data_dir,
        prefilter_threshold=args.prefilter,
        secondary_interval_minutes=args.secondary_interval,
    )
    signal.signal(signal.SIGINT, collector.stop)
    signal.signal(signal.SIGTERM, collector.stop)
    collector.run_forever(interval_seconds=args.interval)


if __name__ == "__main__":
    main()
