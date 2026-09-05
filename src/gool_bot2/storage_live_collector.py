from __future__ import annotations

import argparse
import json
import os
import signal
import time
from concurrent.futures import Future, ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .live_collector import LiveSnapshotCollector
from .prefilter import football_prefilter
from .storage_runtime import PrematchStore, runtime_cleanup


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


class StorageLiveSnapshotCollector(LiveSnapshotCollector):
    """Production collector optimized for hundreds of simultaneous LIVE matches.

    Flashscore's master feed is cheap and remains the source of the complete LIVE
    set. Expensive per-match detail is restricted to the two production entry
    windows and processed concurrently. Dead-window matches never consume stats,
    H2H or secondary-provider time merely because they happen to be LIVE.
    """

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self._prematch_store = PrematchStore()
        self._cleanup_cycles = 0
        detail_workers = max(2, min(24, int(os.getenv("LIVE_DETAIL_WORKERS", "12"))))
        history_workers = max(1, min(8, int(os.getenv("LIVE_HISTORY_WORKERS", "4"))))
        self._detail_pool = ThreadPoolExecutor(max_workers=detail_workers, thread_name_prefix="gool-detail")
        self._history_pool = ThreadPoolExecutor(max_workers=history_workers, thread_name_prefix="gool-history")
        self._history_futures: dict[str, Future[Any]] = {}
        runtime = Path(os.getenv("RUNTIME_DATA_DIR", "data"))
        self._health_path = Path(os.getenv("LIVE_COVERAGE_HEALTH_PATH", str(runtime / "live" / "collector_health.json")))
        self._detail_workers = detail_workers
        self._history_workers = history_workers

    @staticmethod
    def _entry_window(minute: int) -> bool:
        return 1 <= int(minute) <= 35 or 46 <= int(minute) <= 75

    @staticmethod
    def _top_league(league: Any) -> bool:
        text = str(league or "").casefold()
        return any(marker in text for marker in _TOP_LEAGUE_MARKERS)

    @staticmethod
    def _eligible_for_detail(minute: int, is_halftime: bool) -> bool:
        # Halftime is a settlement snapshot, not an expensive detail window.
        return bool(not is_halftime and StorageLiveSnapshotCollector._entry_window(minute))

    def _path_for_now(self, now: datetime) -> Path:
        # Hourly files let us remove stale live data safely without rewriting a file
        # that workers are currently tailing.
        return self.data_dir / now.strftime("%Y-%m-%d-%H.jsonl")

    def _history_for(self, match: Any) -> dict[str, Any]:
        mid = str(match.provider_match_id)
        minimum = int(os.getenv("ANOTHER_GOAL_PREMATCH_MIN_TEAM_MATCHES", "5"))
        cached = self._prematch_context.get(mid) or {}
        if not cached:
            persisted = self._prematch_store.load(mid)
            if persisted and self._history_ready(persisted, minimum):
                self._prematch_context[mid] = persisted
                print(
                    f"PREMATCH_STORE_RESTORE match={mid} home={len(persisted.get('home_recent') or [])} "
                    f"away={len(persisted.get('away_recent') or [])}",
                    flush=True,
                )
                return persisted
        ctx = super()._history_for(match)
        if isinstance(ctx, dict) and ctx:
            self._prematch_store.save(mid, ctx)
        return ctx

    def _cached_history(self, match_id: str) -> dict[str, Any]:
        ctx = self._prematch_context.get(match_id) or {}
        if ctx:
            return ctx
        persisted = self._prematch_store.load(match_id)
        if persisted:
            self._prematch_context[match_id] = persisted
            return persisted
        return {}

    def _schedule_history(self, match: Any) -> None:
        mid = str(match.provider_match_id)
        minimum = int(os.getenv("ANOTHER_GOAL_PREMATCH_MIN_TEAM_MATCHES", "5"))
        cached = self._cached_history(mid)
        if cached and self._history_ready(cached, minimum):
            return
        previous = self._history_futures.get(mid)
        if previous is not None and not previous.done():
            return

        def task() -> None:
            try:
                self._history_for(match)
            except Exception as exc:
                print(f"PREMATCH_BACKGROUND_ERROR match={mid} error={type(exc).__name__}:{exc}", flush=True)

        self._history_futures[mid] = self._history_pool.submit(task)

    def _cheap_record(self, match: Any, now: datetime) -> dict[str, Any]:
        mid = str(match.provider_match_id)
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
                "minute": int(match.minute or 0),
                "home_score": match.home_score,
                "away_score": match.away_score,
                "is_halftime": bool(match.is_halftime),
                "status_code": match.meta.get("status_code", ""),
                "is_finished": False,
            },
            "providers": {
                "flashscore": {
                    "id": match.provider_match_id,
                    "stats": {},
                    "meta": dict(match.meta or {}),
                }
            },
            "prefilter": {"score": 0.0, "candidate": False, "reasons": ["outside_entry_detail_window"]},
            "prematch_ref": mid,
        }
        self._attach_secondary_cache(record, mid)
        return record

    def _active_record(self, match: Any, now: datetime) -> tuple[dict[str, Any], int]:
        mid = str(match.provider_match_id)
        minute = int(match.minute or 0)
        refreshed = self._refresh_secondary(match, minute)

        # Top competitions get synchronous PREMATCH hydration so a premium match
        # can never be evaluated as "history missing" merely because the background
        # queue is busy. Other competitions prefetch asynchronously and remain
        # eligible from LIVE evidence immediately.
        if self._top_league(match.league):
            ctx = self._history_for(match)
        else:
            ctx = self._cached_history(mid)
            if not ctx:
                self._schedule_history(match)

        fs_stats = self.flashscore.fetch_stats(mid)
        goals = self.flashscore.fetch_goal_timeline(mid)
        pref = football_prefilter(fs_stats, minute, threshold=self.prefilter_threshold)
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
                "is_halftime": False,
                "status_code": match.meta.get("status_code", ""),
                "is_finished": False,
            },
            "providers": {
                "flashscore": {
                    "id": match.provider_match_id,
                    "stats": fs_stats,
                    "meta": {**dict(match.meta or {}), "goal_timeline": goals},
                }
            },
            "prefilter": {"score": pref.score, "candidate": pref.candidate, "reasons": list(pref.reasons)},
            "prematch_ref": mid,
        }
        if ctx:
            self._prematch_context[mid] = ctx
            self._prematch_store.save(mid, ctx)
        self._attach_secondary_cache(record, mid)
        if pref.candidate:
            self._attach_flashscore_assets(record, match.meta)
        return record, refreshed

    def _final_record(self, match_id: str, info: dict[str, Any], state: dict[str, Any], now: datetime) -> dict[str, Any]:
        record = super()._final_record(match_id, info, state, now)
        if not (record.get("prematch_context") or {}):
            cached = self._prematch_store.load(match_id)
            if cached:
                # One final embedded copy guarantees settlement still has context
                # even though the sidecar is removed right after the final row is written.
                record["prematch_context"] = cached
        record["prematch_ref"] = match_id
        return record

    def _append(self, record: dict[str, Any], now: datetime) -> None:
        super()._append(record, now)
        match = record.get("match") or {}
        if bool(match.get("is_finished")):
            mid = str(match.get("flashscore_event_id") or "")
            self._prematch_store.delete(mid)
            print(f"PREMATCH_STORE_CLEAN match={mid} reason=finished", flush=True)

    def _write_health(self, payload: dict[str, Any]) -> None:
        try:
            self._health_path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self._health_path.with_suffix(self._health_path.suffix + ".tmp")
            tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), "utf-8")
            tmp.replace(self._health_path)
        except Exception as exc:
            print(f"LIVE_COVERAGE_HEALTH_ERROR {type(exc).__name__}:{exc}", flush=True)

    def collect_once(self) -> dict[str, Any]:
        started = time.monotonic()
        now = datetime.now(timezone.utc)
        matches = self.flashscore.live_matches()
        current_ids = {str(m.provider_match_id) for m in matches}
        active = [m for m in matches if int(m.minute or 0) < 90 and self._entry_window(int(m.minute or 0)) and not m.is_halftime]
        halftime = [m for m in matches if bool(m.is_halftime)]
        late_settlement = [m for m in matches if 76 <= int(m.minute or 0) < 90 and not m.is_halftime]
        dead_first_half = [m for m in matches if 36 <= int(m.minute or 0) <= 45 and not m.is_halftime]

        counters: dict[str, Any] = {
            "live": len(matches),
            "entry_window": len(active),
            "detail": 0,
            "appended": 0,
            "candidate": 0,
            "secondary": 0,
            "settlement_only": 0,
            "skipped_dead_window": len(dead_first_half),
            "final": 0,
            "errors": 0,
            "detail_workers": self._detail_workers,
            "history_workers": self._history_workers,
        }
        top_live = sum(1 for m in matches if self._top_league(m.league))
        top_active = sum(1 for m in active if self._top_league(m.league))
        top_detail = 0

        for match in matches:
            mid = str(match.provider_match_id)
            self._tracked_matches[mid] = {
                "home": match.home,
                "away": match.away,
                "league": match.league,
                "meta": dict(match.meta or {}),
            }

        # Use the otherwise dead 36-45/HT window to warm PREMATCH in the background
        # for the second-half system without delaying score snapshots.
        for match in dead_first_half + halftime:
            self._schedule_history(match)

        futures = {self._detail_pool.submit(self._active_record, match, now): match for match in active}
        for future in as_completed(futures):
            match = futures[future]
            try:
                record, refreshed = future.result()
                self._append(record, now)
                counters["detail"] += 1
                counters["appended"] += 1
                counters["secondary"] += refreshed
                if bool((record.get("prefilter") or {}).get("candidate")):
                    counters["candidate"] += 1
                if self._top_league(match.league):
                    top_detail += 1
            except Exception as exc:
                counters["errors"] += 1
                print(
                    f"LIVE_DETAIL_ERROR match={match.provider_match_id} {match.home}-{match.away} "
                    f"error={type(exc).__name__}:{exc}",
                    flush=True,
                )

        # Halftime and 76-89' are needed for settlement, but do not deserve expensive
        # provider calls because no new ordinary GOOL entry can be created there.
        for match in halftime + late_settlement:
            try:
                self._append(self._cheap_record(match, now), now)
                counters["appended"] += 1
                counters["settlement_only"] += 1
            except Exception as exc:
                counters["errors"] += 1
                print(f"LIVE_SETTLEMENT_SNAPSHOT_ERROR match={match.provider_match_id} error={type(exc).__name__}:{exc}", flush=True)

        missing = set(self._tracked_matches) - current_ids
        if missing:
            try:
                states = self.flashscore.event_states(missing)
                for mid in list(missing):
                    state = states.get(mid)
                    if not state or not bool(state.get("is_finished")):
                        continue
                    final_record = self._final_record(mid, self._tracked_matches[mid], state, now)
                    self._append(final_record, now)
                    self._append({**final_record, "captured_at": datetime.now(timezone.utc).isoformat()}, now)
                    counters["final"] += 1
                    self._tracked_matches.pop(mid, None)
                    self._last_secondary_minute.pop(mid, None)
                    self._secondary_cache.pop(mid, None)
                    self._prematch_context.pop(mid, None)
                    self._prematch_last_attempt.pop(mid, None)
                    self._history_futures.pop(mid, None)
            except Exception as exc:
                counters["errors"] += 1
                print(f"final_state_error={type(exc).__name__}:{exc}", flush=True)

        elapsed = time.monotonic() - started
        coverage = (float(counters["detail"]) / len(active) * 100.0) if active else 100.0
        counters["cycle_ms"] = int(round(elapsed * 1000.0))
        counters["entry_window_coverage_pct"] = round(coverage, 1)
        counters["top_league_live"] = top_live
        counters["top_league_entry_window"] = top_active
        counters["top_league_detail"] = top_detail
        self._write_health({
            "captured_at": datetime.now(timezone.utc).isoformat(),
            "collector": counters,
            "top_league_coverage_ok": top_detail == top_active,
        })
        print(
            f"LIVE_COVERAGE live={len(matches)} active={len(active)} detail={counters['detail']} "
            f"coverage={coverage:.1f}% top={top_detail}/{top_active} cycle={elapsed:.1f}s errors={counters['errors']}",
            flush=True,
        )

        self._cleanup_cycles += 1
        if self._cleanup_cycles >= max(1, int(os.getenv("STORAGE_CLEANUP_EVERY_CYCLES", "5"))):
            self._cleanup_cycles = 0
            result = runtime_cleanup()
            if any(int(v or 0) for v in result.values()):
                print(f"GOOL_STORAGE_RUNTIME {result}", flush=True)
        return counters

    def stop(self, *_: object) -> None:
        super().stop()
        self._detail_pool.shutdown(wait=False, cancel_futures=True)
        self._history_pool.shutdown(wait=False, cancel_futures=True)


def main() -> None:
    parser = argparse.ArgumentParser(description="Collect GOOL live football snapshots with bounded storage")
    parser.add_argument("--data-dir", default=os.getenv("RUNTIME_DATA_DIR", "data") + "/raw/live")
    parser.add_argument("--interval", type=int, default=int(os.getenv("LIVE_INTERVAL_SECONDS", "60")))
    parser.add_argument("--prefilter", type=float, default=float(os.getenv("CORE_ANALYSIS_PREFILTER", "50")))
    parser.add_argument("--secondary-interval", type=int, default=int(os.getenv("SECONDARY_PROVIDER_INTERVAL_MINUTES", "3")))
    args = parser.parse_args()
    collector = StorageLiveSnapshotCollector(
        data_dir=args.data_dir,
        prefilter_threshold=args.prefilter,
        secondary_interval_minutes=args.secondary_interval,
    )
    signal.signal(signal.SIGINT, collector.stop)
    signal.signal(signal.SIGTERM, collector.stop)
    collector.run_forever(interval_seconds=args.interval)


if __name__ == "__main__":
    main()
