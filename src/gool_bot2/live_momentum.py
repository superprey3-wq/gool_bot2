from __future__ import annotations

from collections import defaultdict, deque
from copy import deepcopy
from typing import Any

from .match_context import provider_pair


_KEYS = {
    "xg": "xg",
    "shots": "shots",
    "sot": "shots_on_target",
    "big": "big_chances",
    "danger": "dangerous_attacks",
}


class LiveMomentumTracker:
    """Build real 5m/10m attack deltas from sequential cumulative LIVE snapshots."""

    def __init__(self, max_points: int = 24) -> None:
        self._history: dict[str, deque[dict[str, Any]]] = defaultdict(lambda: deque(maxlen=max_points))

    @staticmethod
    def _point(record: dict[str, Any]) -> dict[str, Any] | None:
        match = record.get("match") or {}
        event_id = str(match.get("flashscore_event_id") or "").strip()
        minute = int(match.get("minute") or 0)
        if not event_id or minute <= 0:
            return None
        point: dict[str, Any] = {
            "event_id": event_id,
            "minute": minute,
            "score": (int(match.get("home_score") or 0), int(match.get("away_score") or 0)),
        }
        for alias, key in _KEYS.items():
            h, a = provider_pair(record, key)
            point[alias] = None if h is None or a is None else (float(h), float(a))
        return point

    @staticmethod
    def _baseline(points: list[dict[str, Any]], minute: int, window: int) -> dict[str, Any] | None:
        target = minute - window
        eligible = [p for p in points if int(p["minute"]) <= target]
        if eligible:
            return max(eligible, key=lambda p: int(p["minute"]))
        older = [p for p in points if int(p["minute"]) < minute]
        return min(older, key=lambda p: int(p["minute"])) if older else None

    def attach(self, record: dict[str, Any]) -> dict[str, Any]:
        point = self._point(record)
        if point is None:
            return record
        event_id = point["event_id"]
        history = self._history[event_id]
        # A score change resets the pressure epoch: pre-goal pressure must not be
        # presented as current post-goal momentum.
        if history and tuple(history[-1]["score"]) != tuple(point["score"]):
            history.clear()
        history.append(point)

        points = list(history)
        first_minute = int(points[0]["minute"])
        momentum: dict[str, Any] = {"minutes_in_epoch": max(0, int(point["minute"]) - first_minute)}
        for window in (5, 10):
            base = self._baseline(points, int(point["minute"]), window)
            if base is None:
                continue
            actual_span = max(1, int(point["minute"]) - int(base["minute"]))
            if actual_span < min(3, window):
                continue
            for alias in _KEYS:
                cur = point.get(alias)
                old = base.get(alias)
                if cur is None or old is None:
                    continue
                hd = max(0.0, float(cur[0]) - float(old[0]))
                ad = max(0.0, float(cur[1]) - float(old[1]))
                momentum[f"home_{alias}_last_{window}m"] = round(hd, 4)
                momentum[f"away_{alias}_last_{window}m"] = round(ad, 4)
                momentum[f"{alias}_total_last_{window}m"] = round(hd + ad, 4)

        out = deepcopy(record)
        out["live_momentum"] = momentum
        return out
