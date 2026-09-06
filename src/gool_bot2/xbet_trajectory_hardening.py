from __future__ import annotations

from typing import Any, Callable


_INSTALLED = False


def _prob(row: dict[str, Any], key: str) -> float | None:
    value = ((row.get("flat") or {}).get(key) or {}).get("prob")
    try:
        return None if value is None else float(value)
    except (TypeError, ValueError):
        return None


def install_xbet_trajectory_hardening() -> None:
    """Attach direction/reversal diagnostics to every 1xBet pressure row.

    Net two-minute movement alone is not enough for STEAM. We also need to know
    whether the move was persistent, whether it has started reversing, and how
    much of the peak probability has already been retraced.
    """
    global _INSTALLED
    if _INSTALLED:
        return

    from . import xbet_market_pressure as xbet

    original: Callable[..., dict[str, Any]] = xbet.XBetMarketCollector._pressure

    def pressure_with_trajectory(
        self: Any,
        fsid: str,
        score: tuple[int, int],
        now: float,
        markets: dict[str, Any],
    ) -> dict[str, Any]:
        result = original(self, fsid, score, now, markets)
        history = list((getattr(self, "snapshots", {}) or {}).get(fsid) or [])
        if len(history) < 2:
            return result

        for key, pressure in result.items():
            values: list[tuple[float, float]] = []
            for row in history:
                value = _prob(row, key)
                if value is None:
                    continue
                try:
                    ts = float(row.get("ts") or 0.0)
                except (TypeError, ValueError):
                    ts = 0.0
                values.append((ts, value))
            if len(values) < 2:
                continue

            up = 0
            down = 0
            flat = 0
            consecutive_up = 0
            for (_, a), (_, b) in zip(values, values[1:]):
                delta = b - a
                if delta > 0.002:
                    up += 1
                elif delta < -0.002:
                    down += 1
                else:
                    flat += 1
            for (_, a), (_, b) in reversed(list(zip(values, values[1:]))):
                if b > a + 0.002:
                    consecutive_up += 1
                else:
                    break

            directional = up / max(1, up + down)
            current = values[-1][1]
            peak = max(value for _, value in values)
            latest_step = (values[-1][1] - values[-2][1]) * 100.0
            span = max(0.0, values[-1][0] - values[0][0])
            pressure.update({
                "up_moves": up,
                "down_moves": down,
                "flat_moves": flat,
                "consecutive_up_moves": consecutive_up,
                "directional_consistency": round(directional, 4),
                "retrace_pp": round(max(0.0, peak - current) * 100.0, 3),
                "latest_step_pp": round(latest_step, 3),
                "trajectory_seconds": round(span, 1),
            })
        return result

    xbet.XBetMarketCollector._pressure = pressure_with_trajectory
    _INSTALLED = True
    print(
        "GOOL_XBET_TRAJECTORY installed direction=reversal+retrace+latest_step window=120s",
        flush=True,
    )


__all__ = ["install_xbet_trajectory_hardening"]
