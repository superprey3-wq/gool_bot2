from __future__ import annotations

import json
import os
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from typing import Any

from . import xbet_market_pressure as market


def extract_xbet_score(game: dict[str, Any] | None) -> tuple[int, int] | None:
    """Return the bookmaker's full-time live score from GetGameZip SC.FS."""
    if not isinstance(game, dict):
        return None
    sc = game.get("SC")
    if not isinstance(sc, dict):
        return None
    fs = sc.get("FS")
    if not isinstance(fs, dict):
        return None
    try:
        home = int(fs.get("S1"))
        away = int(fs.get("S2"))
    except (TypeError, ValueError):
        return None
    if home < 0 or away < 0:
        return None
    return home, away


def _guard_seconds() -> float:
    try:
        return max(12.0, float(os.getenv("XBET_SCORE_REPRICE_GUARD_SECONDS", "24")))
    except Exception:
        return 24.0


def _collect_once_score_safe(self) -> dict[str, Any]:
    started = time.time()
    matches = self.flashscore.live_matches()
    root, candidates = self._fetch_index()
    output: dict[str, Any] = {}

    if not hasattr(self, "_xbet_last_scores"):
        self._xbet_last_scores = {}
    if not hasattr(self, "_xbet_score_changed_at"):
        self._xbet_score_changed_at = {}

    jobs = []
    with ThreadPoolExecutor(max_workers=max(2, int(os.getenv("XBET_GAME_WORKERS", "8")))) as pool:
        for fs in matches:
            event_id = self._map(fs, candidates)
            if event_id:
                jobs.append((fs, event_id, pool.submit(self._game, event_id)))

        for fs, event_id, future in jobs:
            try:
                game = future.result(timeout=12)
            except Exception:
                game = None
            if not game:
                continue

            now = time.time()
            fsid = str(fs.provider_match_id)
            fs_score = (int(fs.home_score or 0), int(fs.away_score or 0))
            xbet_score = extract_xbet_score(game)
            markets = market.decode_markets(game)

            # No bookmaker score = no trustworthy market signal. We intentionally
            # keep the row for diagnostics, but expose no actionable markets.
            if xbet_score is None:
                self.snapshots[fsid] = []
                output[fsid] = {
                    "flashscore_event_id": fsid,
                    "xbet_event_id": event_id,
                    "home": fs.home,
                    "away": fs.away,
                    "minute": int(fs.minute or 0),
                    "score_home": fs_score[0],
                    "score_away": fs_score[1],
                    "flashscore_score_home": fs_score[0],
                    "flashscore_score_away": fs_score[1],
                    "xbet_score_home": None,
                    "xbet_score_away": None,
                    "score_verified": False,
                    "score_desync": False,
                    "repricing_guard": True,
                    "repricing_guard_reason": "XBET_SCORE_UNAVAILABLE",
                    "captured_at": datetime.now(timezone.utc).isoformat(),
                    "markets": {},
                    "pressure": {},
                    "line_move": False,
                }
                continue

            previous = self._xbet_last_scores.get(fsid)
            if previous is not None and tuple(previous) != tuple(xbet_score):
                # A bookmaker score change means all old prices belong to another
                # score epoch. Never compare across that boundary.
                self.snapshots[fsid] = []
                self._xbet_score_changed_at[fsid] = now
                print(
                    f"XBET_SCORE_EPOCH_RESET match={fsid} old={previous[0]}:{previous[1]} "
                    f"new={xbet_score[0]}:{xbet_score[1]}",
                    flush=True,
                )
            self._xbet_last_scores[fsid] = tuple(xbet_score)

            changed_at = self._xbet_score_changed_at.get(fsid)
            guard_age = None if changed_at is None else max(0.0, now - float(changed_at))
            guard_active = changed_at is not None and guard_age < _guard_seconds()
            score_desync = tuple(xbet_score) != tuple(fs_score)

            # During desync or the post-goal repricing window, do not accumulate
            # pressure snapshots. This prevents a goal-driven 1.62 -> 1.15 move
            # from ever becoming STEAM after Flashscore catches up.
            if score_desync or guard_active:
                self.snapshots[fsid] = []
                pressure: dict[str, Any] = {}
                actionable_markets: dict[str, Any] = {}
            else:
                pressure = self._pressure(fsid, tuple(xbet_score), now, markets)
                actionable_markets = markets

            output[fsid] = {
                "flashscore_event_id": fsid,
                "xbet_event_id": event_id,
                "home": fs.home,
                "away": fs.away,
                "minute": int(fs.minute or 0),
                # evaluate_system compares these fields with Flashscore. They now
                # intentionally contain the bookmaker score, not a copy of FS.
                "score_home": xbet_score[0],
                "score_away": xbet_score[1],
                "flashscore_score_home": fs_score[0],
                "flashscore_score_away": fs_score[1],
                "xbet_score_home": xbet_score[0],
                "xbet_score_away": xbet_score[1],
                "score_verified": True,
                "score_desync": score_desync,
                "repricing_guard": bool(guard_active),
                "repricing_guard_seconds": _guard_seconds(),
                "seconds_since_xbet_score_change": None if guard_age is None else round(guard_age, 1),
                "repricing_guard_reason": (
                    "SCORE_DESYNC" if score_desync else ("POST_GOAL_REPRICE" if guard_active else None)
                ),
                "captured_at": datetime.now(timezone.utc).isoformat(),
                "markets": actionable_markets,
                "pressure": pressure,
                "line_move": False,
            }

    state = {
        "captured_at": datetime.now(timezone.utc).isoformat(),
        "root": root,
        "latency_ms": int((time.time() - started) * 1000),
        "matches": output,
    }
    self.state_path.parent.mkdir(parents=True, exist_ok=True)
    tmp = self.state_path.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    tmp.replace(self.state_path)
    self.history_path.parent.mkdir(parents=True, exist_ok=True)
    with self.history_path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(state, ensure_ascii=False, separators=(",", ":")) + "\n")
    return state


def install() -> None:
    if getattr(market.XBetMarketCollector, "_score_epoch_guard_installed", False):
        return
    market.XBetMarketCollector.collect_once = _collect_once_score_safe
    market.XBetMarketCollector._score_epoch_guard_installed = True
