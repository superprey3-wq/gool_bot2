from __future__ import annotations

import argparse
import json
import os
import signal
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .prefilter import football_prefilter
from .providers.flashscore import FlashscoreProvider
from .providers.fotmob import FotMobProvider
from .providers.scores365 import Scores365Provider


class LiveSnapshotCollector:
    """Append-only football snapshot collector for GOOL Bot 2.

    Matches are tracked until Flashscore explicitly changes the coarse event state
    to FINISHED. A displayed 90' is never treated as full time by the collector.
    """

    def __init__(self, data_dir: str | Path = "data/raw/live", prefilter_threshold: float = 50.0, secondary_interval_minutes: int = 3) -> None:
        self.data_dir = Path(data_dir)
        self.prefilter_threshold = float(prefilter_threshold)
        self.secondary_interval_minutes = max(1, int(secondary_interval_minutes))
        self.flashscore = FlashscoreProvider()
        self.fotmob = FotMobProvider()
        self.scores365 = Scores365Provider()
        self._last_secondary_minute: dict[str, int] = {}
        self._tracked_matches: dict[str, dict[str, Any]] = {}
        self._stop = False

    @staticmethod
    def _eligible_for_detail(minute: int, is_halftime: bool) -> bool:
        return bool(is_halftime or 1 <= minute <= 90)

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

    def _attach_flashscore_assets(self, record: dict[str, Any], match_meta: dict[str, Any]) -> None:
        fs = ((record.get("providers") or {}).get("flashscore") or {})
        meta = fs.get("meta") or {}
        home_logo = self.flashscore.team_logo_url(str(match_meta.get("home_team_slug") or ""), str(match_meta.get("home_team_id") or ""))
        away_logo = self.flashscore.team_logo_url(str(match_meta.get("away_team_slug") or ""), str(match_meta.get("away_team_id") or ""))
        if home_logo: meta["home_logo_url"] = home_logo
        if away_logo: meta["away_logo_url"] = away_logo
        fs["meta"] = meta
        record["providers"]["flashscore"] = fs

    def _live_record(self, match: Any, now: datetime) -> dict[str, Any]:
        minute = int(match.minute or 0)
        record: dict[str, Any] = {
            "schema_version": 1,
            "captured_at": now.isoformat(),
            "source_observed_at": now.isoformat(),
            "ingested_at": datetime.now(timezone.utc).isoformat(),
            "match": {
                "flashscore_event_id": match.provider_match_id,
                "home": match.home, "away": match.away, "league": match.league,
                "minute": minute, "home_score": match.home_score, "away_score": match.away_score,
                "is_halftime": match.is_halftime, "status_code": match.meta.get("status_code", ""),
                "is_finished": False,
            },
            "providers": {"flashscore": {"id": match.provider_match_id, "stats": {}, "meta": match.meta}},
            "prefilter": {"score": 0.0, "candidate": False, "reasons": []},
        }
        if self._eligible_for_detail(minute, match.is_halftime):
            fs_stats = self.flashscore.fetch_stats(match.provider_match_id)
            goals = self.flashscore.fetch_goal_timeline(match.provider_match_id)
            record["providers"]["flashscore"] = {"id": match.provider_match_id, "stats": fs_stats, "meta": {**match.meta, "goal_timeline": goals}}
            pref = football_prefilter(fs_stats, minute, threshold=self.prefilter_threshold)
            record["prefilter"] = {"score": pref.score, "candidate": pref.candidate, "reasons": list(pref.reasons)}
        return record

    def _final_record(self, match_id: str, info: dict[str, Any], state: dict[str, Any], now: datetime) -> dict[str, Any]:
        goals = self.flashscore.fetch_goal_timeline(match_id)
        meta = dict(info.get("meta") or {})
        meta.update({"status_code": state.get("status_code", ""), "coarse_status": state.get("coarse_status", "3"), "goal_timeline": goals, "is_finished": True})
        return {
            "schema_version": 1,
            "captured_at": now.isoformat(), "source_observed_at": now.isoformat(), "ingested_at": datetime.now(timezone.utc).isoformat(),
            "match": {
                "flashscore_event_id": match_id,
                "home": info.get("home", "?"), "away": info.get("away", "?"), "league": info.get("league", ""),
                "minute": 90, "home_score": int(state.get("home_score") or 0), "away_score": int(state.get("away_score") or 0),
                "is_halftime": False, "status_code": state.get("status_code", ""), "is_finished": True,
            },
            "providers": {"flashscore": {"id": match_id, "stats": {}, "meta": meta}},
            "prefilter": {"score": 0.0, "candidate": False, "reasons": ["match_finished"]},
        }

    def collect_once(self) -> dict[str, int]:
        now = datetime.now(timezone.utc)
        matches = self.flashscore.live_matches()
        current_ids = {str(m.provider_match_id) for m in matches}
        counters = {"live": len(matches), "detail": 0, "candidate": 0, "secondary": 0, "final": 0, "errors": 0}

        for match in matches:
            try:
                mid = str(match.provider_match_id)
                self._tracked_matches[mid] = {"home": match.home, "away": match.away, "league": match.league, "meta": dict(match.meta or {})}
                minute = int(match.minute or 0)
                # Flashscore caps stoppage time display at 90. Do not feed 90' as a
                # terminal snapshot: wait for coarse status FINISHED instead.
                if minute >= 90:
                    continue
                record = self._live_record(match, now)
                if self._eligible_for_detail(minute, match.is_halftime): counters["detail"] += 1
                pref = record.get("prefilter") or {}
                if pref.get("candidate") or match.is_halftime:
                    self._attach_flashscore_assets(record, match.meta)
                if pref.get("candidate"):
                    counters["candidate"] += 1
                    if self._secondary_due(mid, minute):
                        fm = self.fotmob.enrich(match.home, match.away); sc = self.scores365.enrich(match.home, match.away)
                        if fm: record["providers"][fm.provider] = {"id": fm.provider_match_id, "stats": fm.stats, "meta": fm.meta}
                        if sc: record["providers"][sc.provider] = {"id": sc.provider_match_id, "stats": sc.stats, "meta": sc.meta}
                        counters["secondary"] += int(bool(fm)) + int(bool(sc))
                self._append(record, now)
            except Exception as exc:
                counters["errors"] += 1
                self._append({"schema_version": 1, "captured_at": now.isoformat(), "match": {"flashscore_event_id": match.provider_match_id, "home": match.home, "away": match.away}, "collector_error": f"{type(exc).__name__}: {exc}"}, now)

        # Once a tracked match disappears from LIVE, keep checking the master feed.
        # Only an explicit FINISHED state is allowed to close losing markets.
        missing = set(self._tracked_matches) - current_ids
        if missing:
            try:
                states = self.flashscore.event_states(missing)
                for mid in list(missing):
                    state = states.get(mid)
                    if not state or not bool(state.get("is_finished")):
                        continue
                    final_record = self._final_record(mid, self._tracked_matches[mid], state, now)
                    # Current worker intentionally requires two terminal confirmations
                    # before a loss; duplicate the explicit FINISHED snapshot only.
                    self._append(final_record, now)
                    self._append({**final_record, "captured_at": datetime.now(timezone.utc).isoformat()}, now)
                    counters["final"] += 1
                    self._tracked_matches.pop(mid, None)
                    self._last_secondary_minute.pop(mid, None)
            except Exception as exc:
                counters["errors"] += 1
                print(f"final_state_error={type(exc).__name__}:{exc}", flush=True)
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
                print(json.dumps({"collector_error": f"{type(exc).__name__}: {exc}"}), flush=True)
            elapsed = time.monotonic() - started
            end = time.monotonic() + max(1.0, interval_seconds - elapsed)
            while not self._stop and time.monotonic() < end:
                time.sleep(min(1.0, end - time.monotonic()))


def main() -> None:
    parser = argparse.ArgumentParser(description="Collect GOOL live football snapshots")
    parser.add_argument("--data-dir", default=os.getenv("RUNTIME_DATA_DIR", "data") + "/raw/live")
    parser.add_argument("--interval", type=int, default=int(os.getenv("LIVE_INTERVAL_SECONDS", "60")))
    parser.add_argument("--prefilter", type=float, default=float(os.getenv("CORE_ANALYSIS_PREFILTER", "50")))
    parser.add_argument("--secondary-interval", type=int, default=int(os.getenv("SECONDARY_PROVIDER_INTERVAL_MINUTES", "3")))
    args = parser.parse_args()
    collector = LiveSnapshotCollector(data_dir=args.data_dir, prefilter_threshold=args.prefilter, secondary_interval_minutes=args.secondary_interval)
    signal.signal(signal.SIGINT, collector.stop); signal.signal(signal.SIGTERM, collector.stop)
    collector.run_forever(interval_seconds=args.interval)


if __name__ == "__main__":
    main()
