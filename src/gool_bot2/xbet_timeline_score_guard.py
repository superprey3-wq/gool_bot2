from __future__ import annotations

import json
import os
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from typing import Any

from . import xbet_market_pressure as market


def _guard_seconds() -> float:
    try:
        return max(24.0, float(os.getenv("XBET_TIMELINE_REPRICE_GUARD_SECONDS", "45")))
    except Exception:
        return 45.0


def _timeline_score(provider: Any, event_id: str, coarse: tuple[int, int]) -> tuple[int, int]:
    """Return score implied by Flashscore goal timeline, falling back to master score.

    Unlike the old reconciler this does not take a permanent max: if VAR removes a
    goal and the timeline retracts it, the next snapshot can go back to the coarse
    score and starts a fresh epoch again.
    """
    try:
        goals = provider.fetch_goal_timeline(event_id) or []
    except Exception:
        return coarse
    if not goals:
        return coarse
    last = goals[-1].get("score") or []
    try:
        if len(last) >= 2:
            return int(last[0] or 0), int(last[1] or 0)
    except (TypeError, ValueError):
        pass
    return coarse


def install() -> None:
    if getattr(market.XBetMarketCollector, "_timeline_score_guard_installed", False):
        return

    original = market.XBetMarketCollector.collect_once

    def collect_once(self) -> dict[str, Any]:
        state = original(self)
        matches = state.get("matches") or {}
        if not matches:
            return state

        if not hasattr(self, "_timeline_last_scores"):
            self._timeline_last_scores = {}
        if not hasattr(self, "_timeline_changed_at"):
            self._timeline_changed_at = {}

        ids = list(matches)
        timeline_scores: dict[str, tuple[int, int]] = {}
        with ThreadPoolExecutor(max_workers=max(4, int(os.getenv("XBET_GAME_WORKERS", "8")))) as pool:
            futures = {}
            for fsid in ids:
                row = matches.get(fsid) or {}
                coarse = (
                    int(row.get("flashscore_score_home") or 0),
                    int(row.get("flashscore_score_away") or 0),
                )
                futures[fsid] = pool.submit(_timeline_score, self.flashscore, fsid, coarse)
            for fsid, future in futures.items():
                try:
                    timeline_scores[fsid] = future.result(timeout=12)
                except Exception:
                    row = matches.get(fsid) or {}
                    timeline_scores[fsid] = (
                        int(row.get("flashscore_score_home") or 0),
                        int(row.get("flashscore_score_away") or 0),
                    )

        now = time.time()
        changed = False
        for fsid, row in matches.items():
            coarse = (
                int(row.get("flashscore_score_home") or 0),
                int(row.get("flashscore_score_away") or 0),
            )
            timeline = timeline_scores.get(fsid, coarse)
            previous = self._timeline_last_scores.get(fsid)
            if previous is not None and tuple(previous) != tuple(timeline):
                self.snapshots[fsid] = []
                self._timeline_changed_at[fsid] = now
                print(
                    f"XBET_TIMELINE_EPOCH_RESET match={fsid} old={previous[0]}:{previous[1]} "
                    f"new={timeline[0]}:{timeline[1]}",
                    flush=True,
                )
            elif previous is None and tuple(timeline) != tuple(coarse):
                # Collector started in the narrow window where master score is stale
                # but the event timeline already contains the goal.
                self.snapshots[fsid] = []
                self._timeline_changed_at[fsid] = now
                print(
                    f"XBET_TIMELINE_EPOCH_RESET match={fsid} old={coarse[0]}:{coarse[1]} "
                    f"new={timeline[0]}:{timeline[1]} startup=1",
                    flush=True,
                )
            self._timeline_last_scores[fsid] = tuple(timeline)

            changed_at = self._timeline_changed_at.get(fsid)
            age = None if changed_at is None else max(0.0, now - float(changed_at))
            timeline_guard = changed_at is not None and age < _guard_seconds()

            xbet_score = None
            if row.get("xbet_score_home") is not None and row.get("xbet_score_away") is not None:
                xbet_score = (int(row["xbet_score_home"]), int(row["xbet_score_away"]))

            timeline_master_desync = tuple(timeline) != tuple(coarse)
            timeline_xbet_desync = xbet_score is not None and tuple(timeline) != tuple(xbet_score)
            must_block = timeline_master_desync or timeline_xbet_desync or timeline_guard

            row["flashscore_timeline_score_home"] = timeline[0]
            row["flashscore_timeline_score_away"] = timeline[1]
            row["timeline_score_desync"] = bool(timeline_master_desync or timeline_xbet_desync)
            row["timeline_reprice_guard_seconds"] = _guard_seconds()
            row["seconds_since_timeline_score_change"] = None if age is None else round(age, 1)

            if must_block:
                self.snapshots[fsid] = []
                row["repricing_guard"] = True
                if timeline_master_desync or timeline_xbet_desync:
                    row["repricing_guard_reason"] = "FLASHSCORE_TIMELINE_SCORE_DESYNC"
                elif not row.get("repricing_guard_reason"):
                    row["repricing_guard_reason"] = "POST_GOAL_TIMELINE_REPRICE"
                row["markets"] = {}
                row["pressure"] = {}
                row["line_move"] = False
                changed = True

        if changed:
            state["captured_at"] = datetime.now(timezone.utc).isoformat()
            self.state_path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.state_path.with_suffix(".tmp")
            tmp.write_text(json.dumps(state, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
            tmp.replace(self.state_path)
        return state

    market.XBetMarketCollector.collect_once = collect_once
    market.XBetMarketCollector._timeline_score_guard_installed = True
