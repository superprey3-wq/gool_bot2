from __future__ import annotations

import json
import os
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from typing import Any

from .xbet_market_robust import RobustXBetMarketCollector
from .xbet_score_epoch_guard import (
    _event_guard_seconds,
    _flashscore_red_cards,
    _score_guard_seconds,
    extract_xbet_score,
)
from .xbet_timeline_score_guard import _guard_seconds as _timeline_guard_seconds
from .xbet_timeline_score_guard import _timeline_score


def _shock_threshold_pp() -> float:
    try:
        return max(6.0, float(os.getenv("XBET_ODDS_SHOCK_GUARD_PP", "12")))
    except Exception:
        return 12.0


def _flat(markets: dict[str, Any]) -> dict[str, dict[str, Any]]:
    try:
        return RobustXBetMarketCollector._flat(markets or {})
    except Exception:
        return {}


def _max_probability_jump_pp(previous: dict[str, dict[str, Any]], current: dict[str, dict[str, Any]]) -> float:
    best = 0.0
    for key in set(previous) & set(current):
        old = previous.get(key) or {}
        new = current.get(key) or {}
        try:
            old_p = old.get("prob")
            new_p = new.get("prob")
            if old_p is None or new_p is None:
                continue
            best = max(best, abs(float(new_p) - float(old_p)) * 100.0)
        except (TypeError, ValueError):
            continue
    return best


def _ensure_state(collector: Any) -> None:
    defaults = {
        "_robust_guard_last_xbet_scores": {},
        "_robust_guard_score_changed_at": {},
        "_robust_guard_last_red_cards": {},
        "_robust_guard_last_timeline_scores": {},
        "_robust_guard_event_changed_at": {},
        "_robust_guard_event_reason": {},
        "_robust_guard_last_flat": {},
        "_robust_guard_suspended_at": {},
    }
    for name, value in defaults.items():
        if not hasattr(collector, name):
            setattr(collector, name, dict(value))


def apply_event_guard(
    collector: Any,
    state: dict[str, Any],
    observations: dict[str, dict[str, Any]],
    *,
    now: float | None = None,
) -> bool:
    """Apply score/red-card/VAR/penalty-repricing protection to robust state.

    Direct football events are detected from Flashscore and the 1xBet score epoch.
    Penalties/VAR can suspend or instantly reprice several goal markets without a
    score change, so a suspension/reopen or a single-cycle probability shock is
    treated as event repricing rather than organic steam. Guarded rows expose no
    actionable markets and their pressure history is reset before the next cycle.
    """
    _ensure_state(collector)
    now = time.time() if now is None else float(now)
    matches = state.get("matches") or {}
    changed = False

    for fsid, row in matches.items():
        obs = observations.get(str(fsid)) or {}
        fs_score = (
            int(row.get("score_home") or row.get("flashscore_score_home") or 0),
            int(row.get("score_away") or row.get("flashscore_score_away") or 0),
        )
        xbet_score = obs.get("xbet_score")
        red_cards = obs.get("red_cards")
        timeline_score = obs.get("timeline_score") or fs_score
        raw_markets = dict(row.get("markets") or {})
        current_flat = _flat(raw_markets)
        previous_flat = collector._robust_guard_last_flat.get(str(fsid))

        reason: str | None = None

        if xbet_score is None:
            reason = "XBET_SCORE_UNAVAILABLE"
        else:
            xbet_score = (int(xbet_score[0]), int(xbet_score[1]))
            previous_xbet = collector._robust_guard_last_xbet_scores.get(str(fsid))
            if previous_xbet is not None and tuple(previous_xbet) != tuple(xbet_score):
                collector._robust_guard_score_changed_at[str(fsid)] = now
                collector.snapshots[str(fsid)] = []
                print(
                    f"XBET_ROBUST_SCORE_EPOCH_RESET match={fsid} "
                    f"old={previous_xbet[0]}:{previous_xbet[1]} new={xbet_score[0]}:{xbet_score[1]}",
                    flush=True,
                )
            collector._robust_guard_last_xbet_scores[str(fsid)] = tuple(xbet_score)
            if tuple(xbet_score) != tuple(fs_score):
                reason = "SCORE_DESYNC"

        score_changed_at = collector._robust_guard_score_changed_at.get(str(fsid))
        score_age = None if score_changed_at is None else max(0.0, now - float(score_changed_at))
        if reason is None and score_changed_at is not None and score_age < _score_guard_seconds():
            reason = "POST_GOAL_REPRICE"

        previous_timeline = collector._robust_guard_last_timeline_scores.get(str(fsid))
        timeline_score = (int(timeline_score[0]), int(timeline_score[1]))
        if previous_timeline is not None and tuple(previous_timeline) != tuple(timeline_score):
            collector._robust_guard_event_changed_at[str(fsid)] = now
            collector._robust_guard_event_reason[str(fsid)] = "POST_GOAL_TIMELINE_REPRICE"
            collector.snapshots[str(fsid)] = []
        collector._robust_guard_last_timeline_scores[str(fsid)] = tuple(timeline_score)

        if reason is None and tuple(timeline_score) != tuple(fs_score):
            reason = "FLASHSCORE_TIMELINE_SCORE_DESYNC"
        if reason is None and xbet_score is not None and tuple(timeline_score) != tuple(xbet_score):
            reason = "FLASHSCORE_TIMELINE_SCORE_DESYNC"

        previous_red = collector._robust_guard_last_red_cards.get(str(fsid))
        if red_cards is not None:
            red_cards = (int(red_cards[0]), int(red_cards[1]))
            if previous_red is not None and (red_cards[0] > previous_red[0] or red_cards[1] > previous_red[1]):
                side = "HOME" if red_cards[0] > previous_red[0] else "AWAY"
                collector._robust_guard_event_changed_at[str(fsid)] = now
                collector._robust_guard_event_reason[str(fsid)] = f"EVENT_REPRICE_RED_CARD_{side}"
                collector.snapshots[str(fsid)] = []
                print(
                    f"XBET_ROBUST_EVENT_RESET match={fsid} event=RED_CARD side={side} "
                    f"old={previous_red[0]}:{previous_red[1]} new={red_cards[0]}:{red_cards[1]}",
                    flush=True,
                )
            collector._robust_guard_last_red_cards[str(fsid)] = tuple(red_cards)

        if previous_flat is not None:
            if previous_flat and not current_flat:
                collector._robust_guard_suspended_at[str(fsid)] = now
                collector._robust_guard_event_changed_at[str(fsid)] = now
                collector._robust_guard_event_reason[str(fsid)] = "EVENT_REPRICE_MARKET_SUSPENSION"
                collector.snapshots[str(fsid)] = []
            elif not previous_flat and current_flat:
                suspended_at = collector._robust_guard_suspended_at.get(str(fsid))
                if suspended_at is not None and now - float(suspended_at) <= 90.0:
                    collector._robust_guard_event_changed_at[str(fsid)] = now
                    collector._robust_guard_event_reason[str(fsid)] = "EVENT_REPRICE_MARKET_REOPEN"
                    collector.snapshots[str(fsid)] = []
            elif previous_flat and current_flat:
                jump = _max_probability_jump_pp(previous_flat, current_flat)
                if jump >= _shock_threshold_pp():
                    collector._robust_guard_event_changed_at[str(fsid)] = now
                    collector._robust_guard_event_reason[str(fsid)] = "EVENT_REPRICE_ODDS_SHOCK"
                    collector.snapshots[str(fsid)] = []
                    print(
                        f"XBET_ROBUST_EVENT_RESET match={fsid} event=ODDS_SHOCK jump_pp={jump:.1f}",
                        flush=True,
                    )
        collector._robust_guard_last_flat[str(fsid)] = current_flat

        event_changed_at = collector._robust_guard_event_changed_at.get(str(fsid))
        event_age = None if event_changed_at is None else max(0.0, now - float(event_changed_at))
        event_reason = collector._robust_guard_event_reason.get(str(fsid))
        event_guard = event_changed_at is not None and event_age < max(_event_guard_seconds(), _timeline_guard_seconds())
        if reason is None and event_guard:
            reason = str(event_reason or "EVENT_REPRICE")

        row.update({
            "flashscore_score_home": fs_score[0],
            "flashscore_score_away": fs_score[1],
            "xbet_score_home": None if xbet_score is None else xbet_score[0],
            "xbet_score_away": None if xbet_score is None else xbet_score[1],
            "flashscore_timeline_score_home": timeline_score[0],
            "flashscore_timeline_score_away": timeline_score[1],
            "red_cards_home": None if red_cards is None else red_cards[0],
            "red_cards_away": None if red_cards is None else red_cards[1],
            "score_verified": xbet_score is not None,
            "score_desync": bool(xbet_score is not None and tuple(xbet_score) != tuple(fs_score)),
            "timeline_score_desync": bool(tuple(timeline_score) != tuple(fs_score) or (xbet_score is not None and tuple(timeline_score) != tuple(xbet_score))),
            "score_reprice_guard_seconds": _score_guard_seconds(),
            "event_reprice_guard_seconds": max(_event_guard_seconds(), _timeline_guard_seconds()),
            "odds_shock_guard_pp": _shock_threshold_pp(),
            "seconds_since_xbet_score_change": None if score_age is None else round(score_age, 1),
            "seconds_since_event_change": None if event_age is None else round(event_age, 1),
        })

        if reason is not None:
            collector.snapshots[str(fsid)] = []
            row["repricing_guard"] = True
            row["repricing_guard_reason"] = reason
            row["markets"] = {}
            row["pressure"] = {}
            row["line_move"] = False
            changed = True
        else:
            row["repricing_guard"] = False
            row["repricing_guard_reason"] = None

    return changed


def _persist_corrected_state(collector: Any, state: dict[str, Any]) -> None:
    state["captured_at"] = datetime.now(timezone.utc).isoformat()
    collector.state_path.parent.mkdir(parents=True, exist_ok=True)
    tmp = collector.state_path.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    tmp.replace(collector.state_path)
    collector.history_path.parent.mkdir(parents=True, exist_ok=True)
    with collector.history_path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(state, ensure_ascii=False, separators=(",", ":")) + "\n")


def install() -> None:
    """Install event-repricing protection on RobustXBetMarketCollector itself."""
    if getattr(RobustXBetMarketCollector, "_robust_event_guard_installed", False):
        return

    original_game = RobustXBetMarketCollector._game
    original_collect_once = RobustXBetMarketCollector.collect_once

    def game_with_cycle_cache(self, event_id: str):
        game = original_game(self, event_id)
        cache = getattr(self, "_robust_guard_cycle_games", None)
        if isinstance(cache, dict) and isinstance(game, dict):
            cache[str(event_id)] = game
        return game

    def collect_once(self):
        self._robust_guard_cycle_games = {}
        state = original_collect_once(self)
        matches = state.get("matches") or {}
        if not matches:
            return state

        observations: dict[str, dict[str, Any]] = {}
        with ThreadPoolExecutor(max_workers=max(4, int(os.getenv("XBET_GAME_WORKERS", "8")))) as pool:
            jobs: dict[str, tuple[Any, Any]] = {}
            for fsid, row in matches.items():
                fs_score = (int(row.get("score_home") or 0), int(row.get("score_away") or 0))
                jobs[str(fsid)] = (
                    pool.submit(_flashscore_red_cards, self.flashscore, str(fsid)),
                    pool.submit(_timeline_score, self.flashscore, str(fsid), fs_score),
                )
            for fsid, (red_future, timeline_future) in jobs.items():
                row = matches.get(fsid) or {}
                event_id = str(row.get("xbet_event_id") or "")
                game = (getattr(self, "_robust_guard_cycle_games", {}) or {}).get(event_id)
                try:
                    red_cards = red_future.result(timeout=12)
                except Exception:
                    red_cards = None
                try:
                    timeline_score = timeline_future.result(timeout=12)
                except Exception:
                    timeline_score = (int(row.get("score_home") or 0), int(row.get("score_away") or 0))
                observations[fsid] = {
                    "xbet_score": extract_xbet_score(game),
                    "red_cards": red_cards,
                    "timeline_score": timeline_score,
                }

        changed = apply_event_guard(self, state, observations)
        if changed:
            _persist_corrected_state(self, state)
        return state

    RobustXBetMarketCollector._game = game_with_cycle_cache
    RobustXBetMarketCollector.collect_once = collect_once
    RobustXBetMarketCollector._robust_event_guard_installed = True
