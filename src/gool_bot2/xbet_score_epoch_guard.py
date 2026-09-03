from __future__ import annotations

import json
import os
import re
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from typing import Any

from . import xbet_market_pressure as market


def extract_xbet_score(game: dict[str, Any] | None) -> tuple[int, int] | None:
    """Return the bookmaker live score from GetGameZip ``SC.FS``.

    1xBet suppresses zero-valued sides in many football responses: ``{}`` means
    0:0, ``{"S1": 3}`` means 3:0 and ``{"S2": 1}`` means 0:1. Treat a missing
    S1/S2 inside an otherwise valid FS dictionary as zero, while still rejecting
    a missing/non-dict FS object. Score/timeline guards then independently catch
    any disagreement with Flashscore before a market can become actionable.
    """
    if not isinstance(game, dict):
        return None
    sc = game.get("SC")
    if not isinstance(sc, dict):
        return None
    fs = sc.get("FS")
    if not isinstance(fs, dict):
        return None
    try:
        home = int(fs.get("S1", 0) or 0)
        away = int(fs.get("S2", 0) or 0)
    except (TypeError, ValueError):
        return None
    return (home, away) if home >= 0 and away >= 0 else None


def _score_guard_seconds() -> float:
    try: return max(12.0, float(os.getenv("XBET_SCORE_REPRICE_GUARD_SECONDS", "24")))
    except Exception: return 24.0


def _event_guard_seconds() -> float:
    try: return max(24.0, float(os.getenv("XBET_EVENT_REPRICE_GUARD_SECONDS", "45")))
    except Exception: return 45.0


def _flashscore_red_cards(provider: Any, event_id: str) -> tuple[int, int] | None:
    """Read red-card counter directly from Flashscore stats feed (stat id 24).

    This intentionally bypasses the public stats mapping so the market collector can
    detect a dismissal independently of the slower GOOL snapshot collector.
    """
    try:
        body = provider._feed(f"df_st_1_{event_id}")
    except Exception:
        return None
    for chunk in (body or "").split("~"):
        m = re.search(r"SD(?:÷|¬)24.*?SH(?:÷|¬)([^¬~]+).*?SI(?:÷|¬)([^¬~]+)", chunk)
        if not m:
            continue
        try:
            return int(float(m.group(1))), int(float(m.group(2)))
        except (TypeError, ValueError):
            return None
    return (0, 0)


def _guarded_evaluate_system(original):
    def evaluate(market_row, head, score_home, score_away, selected_side=None):
        if isinstance(market_row, dict) and market_row.get("repricing_guard"):
            reason = str(market_row.get("repricing_guard_reason") or "EVENT_REPRICE")
            return {
                "available": True,
                "confirmed": False,
                "level": reason,
                "reason": f"1xBet blocked: {reason}",
                "head": head,
                "event_reprice": reason.startswith("EVENT_REPRICE"),
                "event_reprice_reason": reason,
                "targets": [],
                "score_pp": 0.0,
            }
        return original(market_row, head, score_home, score_away, selected_side)
    return evaluate


def _collect_once_score_safe(self) -> dict[str, Any]:
    started = time.time(); matches = self.flashscore.live_matches(); root, candidates = self._fetch_index(); output: dict[str, Any] = {}
    if not hasattr(self, "_xbet_last_scores"): self._xbet_last_scores = {}
    if not hasattr(self, "_xbet_score_changed_at"): self._xbet_score_changed_at = {}
    if not hasattr(self, "_xbet_last_red_cards"): self._xbet_last_red_cards = {}
    if not hasattr(self, "_xbet_event_changed_at"): self._xbet_event_changed_at = {}
    if not hasattr(self, "_xbet_event_reason"): self._xbet_event_reason = {}

    jobs = []
    with ThreadPoolExecutor(max_workers=max(4, int(os.getenv("XBET_GAME_WORKERS", "8")))) as pool:
        for fs in matches:
            event_id = self._map(fs, candidates)
            if event_id:
                fsid = str(fs.provider_match_id)
                jobs.append((fs, event_id, pool.submit(self._game, event_id), pool.submit(_flashscore_red_cards, self.flashscore, fsid)))

        for fs, event_id, game_future, cards_future in jobs:
            try: game = game_future.result(timeout=12)
            except Exception: game = None
            try: red_cards = cards_future.result(timeout=12)
            except Exception: red_cards = None
            if not game: continue

            now = time.time(); fsid = str(fs.provider_match_id)
            fs_score = (int(fs.home_score or 0), int(fs.away_score or 0)); xbet_score = extract_xbet_score(game)
            markets = market.decode_markets(game)

            if xbet_score is None:
                self.snapshots[fsid] = []
                output[fsid] = {"flashscore_event_id":fsid,"xbet_event_id":event_id,"home":fs.home,"away":fs.away,"minute":int(fs.minute or 0),"score_home":fs_score[0],"score_away":fs_score[1],"flashscore_score_home":fs_score[0],"flashscore_score_away":fs_score[1],"xbet_score_home":None,"xbet_score_away":None,"score_verified":False,"score_desync":False,"repricing_guard":True,"repricing_guard_reason":"XBET_SCORE_UNAVAILABLE","captured_at":datetime.now(timezone.utc).isoformat(),"markets":{},"pressure":{},"line_move":False}
                continue

            previous_score = self._xbet_last_scores.get(fsid)
            if previous_score is not None and tuple(previous_score) != tuple(xbet_score):
                self.snapshots[fsid] = []; self._xbet_score_changed_at[fsid] = now
                print(f"XBET_SCORE_EPOCH_RESET match={fsid} old={previous_score[0]}:{previous_score[1]} new={xbet_score[0]}:{xbet_score[1]}", flush=True)
            self._xbet_last_scores[fsid] = tuple(xbet_score)

            previous_red = self._xbet_last_red_cards.get(fsid)
            if red_cards is not None:
                if previous_red is not None and (red_cards[0] > previous_red[0] or red_cards[1] > previous_red[1]):
                    side = "HOME" if red_cards[0] > previous_red[0] else "AWAY"
                    self.snapshots[fsid] = []; self._xbet_event_changed_at[fsid] = now
                    self._xbet_event_reason[fsid] = f"EVENT_REPRICE_RED_CARD_{side}"
                    print(f"XBET_EVENT_EPOCH_RESET match={fsid} event=RED_CARD side={side} old={previous_red[0]}:{previous_red[1]} new={red_cards[0]}:{red_cards[1]}", flush=True)
                self._xbet_last_red_cards[fsid] = tuple(red_cards)

            score_changed_at = self._xbet_score_changed_at.get(fsid)
            score_age = None if score_changed_at is None else max(0.0, now-float(score_changed_at))
            score_guard = score_changed_at is not None and score_age < _score_guard_seconds()
            event_changed_at = self._xbet_event_changed_at.get(fsid)
            event_age = None if event_changed_at is None else max(0.0, now-float(event_changed_at))
            event_guard = event_changed_at is not None and event_age < _event_guard_seconds()
            score_desync = tuple(xbet_score) != tuple(fs_score)
            guard_active = bool(score_desync or score_guard or event_guard)
            if score_desync: guard_reason = "SCORE_DESYNC"
            elif score_guard: guard_reason = "POST_GOAL_REPRICE"
            elif event_guard: guard_reason = str(self._xbet_event_reason.get(fsid) or "EVENT_REPRICE")
            else: guard_reason = None

            if guard_active:
                self.snapshots[fsid] = []; pressure = {}; actionable_markets = {}
            else:
                pressure = self._pressure(fsid, tuple(xbet_score), now, markets); actionable_markets = markets

            output[fsid] = {
                "flashscore_event_id":fsid,"xbet_event_id":event_id,"home":fs.home,"away":fs.away,"minute":int(fs.minute or 0),
                "score_home":xbet_score[0],"score_away":xbet_score[1],"flashscore_score_home":fs_score[0],"flashscore_score_away":fs_score[1],
                "xbet_score_home":xbet_score[0],"xbet_score_away":xbet_score[1],"score_verified":True,"score_desync":score_desync,
                "red_cards_home":None if red_cards is None else red_cards[0],"red_cards_away":None if red_cards is None else red_cards[1],
                "repricing_guard":guard_active,"repricing_guard_reason":guard_reason,
                "score_reprice_guard_seconds":_score_guard_seconds(),"event_reprice_guard_seconds":_event_guard_seconds(),
                "seconds_since_xbet_score_change":None if score_age is None else round(score_age,1),
                "seconds_since_event_change":None if event_age is None else round(event_age,1),
                "captured_at":datetime.now(timezone.utc).isoformat(),"markets":actionable_markets,"pressure":pressure,"line_move":False,
            }

    state={"captured_at":datetime.now(timezone.utc).isoformat(),"root":root,"latency_ms":int((time.time()-started)*1000),"matches":output}
    self.state_path.parent.mkdir(parents=True,exist_ok=True); tmp=self.state_path.with_suffix(".tmp"); tmp.write_text(json.dumps(state,ensure_ascii=False,separators=(",",":")),encoding="utf-8"); tmp.replace(self.state_path)
    self.history_path.parent.mkdir(parents=True,exist_ok=True)
    with self.history_path.open("a",encoding="utf-8") as fh: fh.write(json.dumps(state,ensure_ascii=False,separators=(",",":"))+"\n")
    return state


def install() -> None:
    if getattr(market.XBetMarketCollector, "_score_epoch_guard_installed", False): return
    market.XBetMarketCollector.collect_once = _collect_once_score_safe
    market.evaluate_system = _guarded_evaluate_system(market.evaluate_system)
    market.XBetMarketCollector._score_epoch_guard_installed = True
