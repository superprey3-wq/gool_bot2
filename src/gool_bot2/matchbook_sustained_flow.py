from __future__ import annotations

import math
import os
from collections import defaultdict, deque
from pathlib import Path
from typing import Any

from .matchbook_exchange import MatchbookExchangeCollector, _number

_WINDOWS: tuple[tuple[float, str], ...] = (
    (15.0, "15s"),
    (30.0, "30s"),
    (60.0, "60s"),
    (120.0, "120s"),
    (180.0, "180s"),
)
_LONG_WINDOWS = ("60s", "120s", "180s")
_SECONDS = {label: int(seconds) for seconds, label in _WINDOWS}
_SUPPORT = {"SUPPORT", "STRONG_SUPPORT"}
_OPPOSITION = {"OPPOSITION", "STRONG_OPPOSITION"}


def _f(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        return default


class SustainedMatchbookExchangeCollector(MatchbookExchangeCollector):
    """Matchbook collector with exact 1/2/3-minute money-flow windows.

    Matched volume alone is not directional. A window is classified as support
    or opposition only when meaningful new matched volume arrives *and* the
    de-vigged over probability moves in the same direction. Two aligned long
    windows, including at least 120 seconds, become a sustained flow signal.
    """

    def __init__(self, state_path: Path) -> None:
        super().__init__(state_path)
        # 24 snapshots keep six minutes at the production 15s cadence, enough
        # for an exact 180s baseline even with normal scheduling jitter.
        self._history = defaultdict(lambda: deque(maxlen=24))

    def _flow(self, event_id: str, key: str, market: dict[str, Any], now: float) -> dict[str, Any]:
        fair = _number(market.get("fair_over"))
        volume = float(_number(market.get("volume")) or 0.0)
        hist = self._history[self._hist_key(event_id, key)]
        current = {
            "ts": float(now),
            "fair": float(fair) if fair is not None else math.nan,
            "volume": volume,
        }

        def prior(seconds: float) -> dict[str, float] | None:
            candidates = [row for row in hist if now - float(row["ts"]) >= seconds]
            return candidates[-1] if candidates else None

        min_volume = max(0.0, _f("MATCHBOOK_FLOW_MIN_VOLUME_DELTA", 20.0))
        strong_volume = max(min_volume, _f("MATCHBOOK_FLOW_STRONG_VOLUME_DELTA", 75.0))
        min_rate = max(0.0, _f("MATCHBOOK_FLOW_MIN_VOLUME_PER_MIN", 20.0))
        strong_rate = max(min_rate, _f("MATCHBOOK_FLOW_STRONG_VOLUME_PER_MIN", 50.0))
        min_share = max(0.0, _f("MATCHBOOK_FLOW_MIN_SHARE_PCT", 2.0))
        strong_share = max(min_share, _f("MATCHBOOK_FLOW_STRONG_SHARE_PCT", 6.0))
        min_move = max(0.1, _f("MATCHBOOK_FLOW_MIN_MOVE_PP", 1.25))
        strong_move = max(min_move, _f("MATCHBOOK_FLOW_STRONG_MOVE_PP", 3.0))

        out: dict[str, Any] = {}
        ready: dict[str, bool] = {}
        levels: dict[str, str] = {}

        for seconds, label in _WINDOWS:
            old = prior(seconds)
            ready[label] = old is not None
            out[f"window_ready_{label}"] = ready[label]
            if old is None:
                out[f"volume_delta_{label}"] = 0.0
                out[f"volume_rate_per_min_{label}"] = 0.0
                out[f"volume_share_pct_{label}"] = 0.0
                out[f"fair_over_delta_pp_{label}"] = 0.0
                out[f"window_level_{label}"] = "NEUTRAL"
                levels[label] = "NEUTRAL"
                continue

            old_volume = max(0.0, float(old["volume"]))
            volume_delta = max(0.0, volume - old_volume)
            rate = volume_delta / max(seconds / 60.0, 1e-9)
            share = (volume_delta / old_volume * 100.0) if old_volume > 0.0 else 0.0
            old_fair = float(old["fair"])
            if fair is None or math.isnan(old_fair):
                delta_pp = 0.0
            else:
                delta_pp = (float(fair) - old_fair) * 100.0

            active = volume_delta >= min_volume and (rate >= min_rate or share >= min_share)
            strongly_active = volume_delta >= strong_volume and (
                rate >= strong_rate or share >= strong_share
            )
            if delta_pp >= strong_move and strongly_active:
                level = "STRONG_SUPPORT"
            elif delta_pp >= min_move and active:
                level = "SUPPORT"
            elif delta_pp <= -strong_move and strongly_active:
                level = "STRONG_OPPOSITION"
            elif delta_pp <= -min_move and active:
                level = "OPPOSITION"
            else:
                level = "NEUTRAL"

            out[f"volume_delta_{label}"] = round(volume_delta, 4)
            out[f"volume_rate_per_min_{label}"] = round(rate, 4)
            out[f"volume_share_pct_{label}"] = round(share, 3)
            out[f"fair_over_delta_pp_{label}"] = round(delta_pp, 3)
            out[f"window_level_{label}"] = level
            levels[label] = level

        hist.append(current)

        support_windows = [label for label in _LONG_WINDOWS if levels.get(label) in _SUPPORT]
        opposition_windows = [label for label in _LONG_WINDOWS if levels.get(label) in _OPPOSITION]
        sustained = False
        sustained_direction: str | None = None
        sustained_windows: list[str] = []

        if len(support_windows) >= 2 and any(_SECONDS[label] >= 120 for label in support_windows):
            sustained = True
            sustained_direction = "support"
            sustained_windows = support_windows
        elif len(opposition_windows) >= 2 and any(_SECONDS[label] >= 120 for label in opposition_windows):
            sustained = True
            sustained_direction = "opposition"
            sustained_windows = opposition_windows

        signal_label: str | None = None
        level = "NEUTRAL"
        if sustained and sustained_windows:
            signal_label = max(sustained_windows, key=lambda label: _SECONDS[label])
            longest_level = levels.get(signal_label, "NEUTRAL")
            if sustained_direction == "support":
                level = "STRONG_SUPPORT" if longest_level == "STRONG_SUPPORT" else "SUPPORT"
            else:
                level = "STRONG_OPPOSITION" if longest_level == "STRONG_OPPOSITION" else "OPPOSITION"
        else:
            # Prefer the longer short-term window. A fresh 30s impulse still
            # remains visible when the 60s window is not directional yet.
            for label in ("60s", "30s"):
                if ready.get(label) and levels.get(label) != "NEUTRAL":
                    signal_label = label
                    level = levels[label]
                    break
            if signal_label is None:
                for label in ("60s", "30s", "15s"):
                    if ready.get(label):
                        signal_label = label
                        break

        signal_seconds = _SECONDS.get(signal_label or "", 0)
        direction_pp = float(out.get(f"fair_over_delta_pp_{signal_label}") or 0.0) if signal_label else 0.0
        activity_volume = float(out.get(f"volume_delta_{signal_label}") or 0.0) if signal_label else 0.0
        activity_rate = float(out.get(f"volume_rate_per_min_{signal_label}") or 0.0) if signal_label else 0.0
        activity_share = float(out.get(f"volume_share_pct_{signal_label}") or 0.0) if signal_label else 0.0

        out.update(
            {
                "level": level,
                "direction_pp": round(direction_pp, 3),
                "activity_volume": round(activity_volume, 4),
                "activity_rate_per_min": round(activity_rate, 4),
                "activity_share_pct": round(activity_share, 3),
                "signal_window": signal_label,
                "signal_window_seconds": signal_seconds,
                "sustained": sustained,
                "sustained_direction": sustained_direction,
                "sustained_windows": sustained_windows,
            }
        )
        return out
