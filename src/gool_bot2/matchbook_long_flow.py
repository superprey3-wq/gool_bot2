from __future__ import annotations

import math
import time
from collections import deque
from typing import Any, Callable


_INSTALLED = False
_LONG_HISTORY: dict[str, deque[dict[str, float]]] = {}
_CALLS = 0


def _number(value: Any) -> float | None:
    try:
        if value is None:
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _fair(market: dict[str, Any]) -> float | None:
    value = _number(market.get("fair_over"))
    return value if value is not None and not math.isnan(value) else None


def _cleanup(now: float) -> None:
    stale = [
        key
        for key, rows in _LONG_HISTORY.items()
        if not rows or now - float(rows[-1].get("ts") or 0.0) > 900.0
    ]
    for key in stale:
        _LONG_HISTORY.pop(key, None)


def install_matchbook_long_flow() -> None:
    """Extend Matchbook flow from seconds to two/five-minute accumulation windows."""
    global _INSTALLED
    if _INSTALLED:
        return

    from . import matchbook_exchange as exchange

    original: Callable[..., dict[str, Any]] = exchange.MatchbookExchangeCollector._flow

    def flow_with_long_memory(
        self: Any,
        event_id: str,
        key: str,
        market: dict[str, Any],
        now: float,
    ) -> dict[str, Any]:
        global _CALLS
        out = dict(original(self, event_id, key, market, now) or {})
        hist_key = f"{event_id}:{key}"
        rows = _LONG_HISTORY.setdefault(hist_key, deque(maxlen=64))
        while rows and now - float(rows[0].get("ts") or 0.0) > 600.0:
            rows.popleft()

        volume = max(0.0, float(_number(market.get("volume")) or 0.0))
        fair = _fair(market)

        def prior(seconds: float) -> dict[str, float] | None:
            candidates = [row for row in rows if now - float(row.get("ts") or 0.0) >= seconds]
            return candidates[-1] if candidates else None

        for seconds, label in ((120.0, "120s"), (300.0, "300s")):
            old = prior(seconds)
            out[f"window_ready_{label}"] = old is not None
            if old is None:
                out[f"volume_delta_{label}"] = 0.0
                out[f"fair_over_delta_pp_{label}"] = 0.0
                out[f"relative_volume_delta_pct_{label}"] = 0.0
                continue
            old_volume = max(0.0, float(old.get("volume") or 0.0))
            delta = max(0.0, volume - old_volume)
            old_fair = old.get("fair")
            fair_pp = 0.0 if fair is None or old_fair is None or math.isnan(float(old_fair)) else (fair - float(old_fair)) * 100.0
            out[f"volume_delta_{label}"] = round(delta, 4)
            out[f"fair_over_delta_pp_{label}"] = round(fair_pp, 3)
            out[f"relative_volume_delta_pct_{label}"] = round(delta / max(1.0, old_volume) * 100.0, 3)

        trajectory = [row for row in rows if now - float(row.get("ts") or 0.0) <= 300.0]
        if fair is not None:
            trajectory = [*trajectory, {"ts": now, "volume": volume, "fair": fair}]
        up = down = 0
        for a, b in zip(trajectory, trajectory[1:]):
            af = a.get("fair")
            bf = b.get("fair")
            if af is None or bf is None:
                continue
            delta = float(bf) - float(af)
            if delta > 0.001:
                up += 1
            elif delta < -0.001:
                down += 1
        out["long_direction_consistency"] = round(up / max(1, up + down), 4)
        out["long_up_moves"] = up
        out["long_down_moves"] = down

        rows.append({
            "ts": float(now),
            "volume": volume,
            "fair": math.nan if fair is None else float(fair),
        })
        _CALLS += 1
        if _CALLS % 500 == 0:
            _cleanup(now)
        return out

    exchange.MatchbookExchangeCollector._flow = flow_with_long_memory
    _INSTALLED = True
    print(
        "GOOL_MATCHBOOK_LONG_FLOW installed windows=120s/300s accumulation=enabled",
        flush=True,
    )


__all__ = ["install_matchbook_long_flow"]
