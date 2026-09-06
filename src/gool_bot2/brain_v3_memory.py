from __future__ import annotations

import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from .match_context import provider_pair


_INSTALLED = False
_HISTORY: dict[str, list[dict[str, Any]]] = {}
_TRACE_WRITES = 0


def _number(value: Any) -> float | None:
    try:
        if value is None:
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _pair(record: dict[str, Any], key: str) -> tuple[float | None, float | None]:
    try:
        home, away = provider_pair(record, key)
    except Exception:
        return None, None
    return _number(home), _number(away)


def _safe_delta(current: Any, previous: Any) -> float | None:
    a = _number(current)
    b = _number(previous)
    if a is None or b is None:
        return None
    return max(0.0, a - b)


def _clamp(value: float, lo: float = 0.0, hi: float = 1.0) -> float:
    return max(lo, min(hi, float(value)))


def _period(minute: int, halftime: bool) -> str:
    if halftime:
        return "HT"
    if 0 < minute <= 45:
        return "1H"
    if minute >= 46:
        return "2H"
    return "PRE"


def _snapshot(record: dict[str, Any], match_id: str) -> dict[str, Any]:
    match = record.get("match") or {}
    stats: dict[str, Any] = {}
    observed_pairs = 0
    for key, alias in (
        ("shots", "shots"),
        ("shots_on_target", "sot"),
        ("xg", "xg"),
        ("big_chances", "big"),
        ("dangerous_attacks", "danger"),
        ("corners", "corners"),
        ("attacks", "attacks"),
    ):
        home, away = _pair(record, key)
        stats[f"home_{alias}"] = home
        stats[f"away_{alias}"] = away
        if home is not None and away is not None:
            observed_pairs += 1

    minute = int(match.get("minute") or 0)
    halftime = bool(match.get("is_halftime"))
    return {
        "match_id": match_id,
        "captured_at": str(record.get("captured_at") or datetime.now(timezone.utc).isoformat()),
        "minute": minute,
        "period": _period(minute, halftime),
        "score": [int(match.get("home_score") or 0), int(match.get("away_score") or 0)],
        "halftime": halftime,
        "observed_pairs": observed_pairs,
        "usable": observed_pairs >= 2,
        **stats,
    }


def _same_score(row: dict[str, Any], current: dict[str, Any]) -> bool:
    return list(row.get("score") or []) == list(current.get("score") or [])


def _same_epoch(row: dict[str, Any], current: dict[str, Any]) -> bool:
    return _same_score(row, current) and str(row.get("period") or "") == str(current.get("period") or "")


def _previous_for_window(
    history: list[dict[str, Any]],
    current: dict[str, Any],
    window: int,
) -> dict[str, Any] | None:
    if not bool(current.get("usable")):
        return None
    target_minute = int(current.get("minute") or 0) - int(window)
    candidates = [
        row
        for row in history[:-1]
        if bool(row.get("usable"))
        and _same_epoch(row, current)
        and int(row.get("minute") or 0) <= target_minute
    ]
    if candidates:
        return max(candidates, key=lambda row: int(row.get("minute") or 0))
    return None


def _window_delta(current: dict[str, Any], previous: dict[str, Any], window: int) -> dict[str, Any]:
    out: dict[str, Any] = {
        "window_minutes": int(window),
        "from_minute": int(previous.get("minute") or 0),
        "to_minute": int(current.get("minute") or 0),
        "period": current.get("period"),
    }
    for alias in ("shots", "sot", "xg", "big", "danger", "corners", "attacks"):
        for side in ("home", "away"):
            out[f"{side}_{alias}"] = _safe_delta(
                current.get(f"{side}_{alias}"),
                previous.get(f"{side}_{alias}"),
            )
    return out


def _xg_equivalent(window: dict[str, Any], side: str) -> tuple[float | None, str]:
    actual = _number(window.get(f"{side}_xg"))
    if actual is not None:
        return max(0.0, actual), "provider_xg"

    pieces = (
        ("shots", 0.025),
        ("sot", 0.070),
        ("big", 0.180),
        ("danger", 0.0035),
    )
    total = 0.0
    evidence = 0
    for alias, weight in pieces:
        value = _number(window.get(f"{side}_{alias}"))
        if value is None:
            continue
        evidence += 1
        total += max(0.0, value) * weight
    if evidence < 2:
        return None, "unavailable"
    return max(0.0, total), "attack_proxy"


def _pressure_score(window: dict[str, Any] | None, side: str) -> tuple[float | None, dict[str, Any]]:
    if not window:
        return None, {"available": False}
    xg_equiv, source = _xg_equivalent(window, side)
    shots = _number(window.get(f"{side}_shots"))
    sot = _number(window.get(f"{side}_sot"))
    big = _number(window.get(f"{side}_big"))
    danger = _number(window.get(f"{side}_danger"))

    parts: list[tuple[float, float]] = []
    if xg_equiv is not None:
        parts.append((_clamp(xg_equiv / 0.38), 0.42))
    if sot is not None:
        parts.append((_clamp(sot / 2.2), 0.24))
    if shots is not None:
        parts.append((_clamp(shots / 5.0), 0.15))
    if big is not None:
        parts.append((_clamp(big / 1.0), 0.12))
    if danger is not None:
        parts.append((_clamp(danger / 20.0), 0.07))
    if not parts:
        return None, {"available": False, "xg_source": source}
    weight = sum(w for _, w in parts)
    score = sum(value * w for value, w in parts) / weight
    return round(_clamp(score), 4), {
        "available": True,
        "xg_equiv": None if xg_equiv is None else round(xg_equiv, 4),
        "xg_source": source,
        "shots": shots,
        "sot": sot,
        "big": big,
        "danger": danger,
    }


def _segment(window10: dict[str, Any] | None, window5: dict[str, Any] | None, side: str, alias: str) -> float | None:
    if not window10 or not window5:
        return None
    a = _number(window10.get(f"{side}_{alias}"))
    b = _number(window5.get(f"{side}_{alias}"))
    if a is None or b is None:
        return None
    return max(0.0, a - b)


def _trend(window5: dict[str, Any] | None, window10: dict[str, Any] | None, side: str) -> dict[str, Any]:
    if not window5 or not window10:
        return {"state": "WARMING", "ratio": None}

    recent_proxy, _ = _xg_equivalent(window5, side)
    prior5 = {
        f"{side}_{alias}": _segment(window10, window5, side, alias)
        for alias in ("shots", "sot", "xg", "big", "danger")
    }
    prior_proxy, _ = _xg_equivalent(prior5, side)
    if recent_proxy is None or prior_proxy is None:
        return {"state": "STEADY", "ratio": None}

    ratio = recent_proxy / max(0.03, prior_proxy)
    if recent_proxy >= 0.16 and ratio >= 1.30:
        state = "RISING"
    elif prior_proxy >= 0.16 and ratio <= 0.65:
        state = "FALLING"
    else:
        state = "STEADY"
    return {
        "state": state,
        "ratio": round(ratio, 3),
        "recent5_xg_equiv": round(recent_proxy, 4),
        "prior5_xg_equiv": round(prior_proxy, 4),
    }


def _epoch(history: list[dict[str, Any]], current: dict[str, Any]) -> dict[str, Any]:
    same: list[dict[str, Any]] = []
    for row in reversed(history):
        if not _same_epoch(row, current):
            break
        if bool(row.get("usable")):
            same.append(row)
    same.reverse()
    start = same[0] if same else current
    return {
        "score": list(current.get("score") or [0, 0]),
        "period": current.get("period"),
        "start_minute": int(start.get("minute") or 0),
        "age_minutes": max(0, int(current.get("minute") or 0) - int(start.get("minute") or 0)),
        "samples": len(same),
    }


def _classify(
    home_score: float | None,
    away_score: float | None,
    home_trend: dict[str, Any],
    away_trend: dict[str, Any],
    epoch: dict[str, Any],
) -> str:
    if int(epoch.get("age_minutes") or 0) < 2:
        return "POST_GOAL_RESET" if sum(epoch.get("score") or [0, 0]) > 0 else "WARMING"
    if home_score is None and away_score is None:
        return "NO_DATA"
    h = float(home_score or 0.0)
    a = float(away_score or 0.0)
    if h >= 0.72 and h - a >= 0.18:
        return "HOME_SIEGE"
    if a >= 0.72 and a - h >= 0.18:
        return "AWAY_SIEGE"
    if h >= 0.50 and a >= 0.50:
        return "END_TO_END"
    if h >= 0.50 and h - a >= 0.12:
        return "HOME_PRESSURE"
    if a >= 0.50 and a - h >= 0.12:
        return "AWAY_PRESSURE"
    if home_trend.get("state") == "RISING" and h >= 0.38:
        return "HOME_BUILDING"
    if away_trend.get("state") == "RISING" and a >= 0.38:
        return "AWAY_BUILDING"
    return "CALM"


def build_brain_v3_memory(record: dict[str, Any], match_id: str) -> dict[str, Any]:
    current = _snapshot(record, match_id)
    history = list(_HISTORY.get(match_id) or [])

    history = [
        row for row in history
        if not (
            int(row.get("minute") or 0) == int(current.get("minute") or 0)
            and _same_epoch(row, current)
        )
    ]
    history.append(current)
    history.sort(key=lambda row: (int(row.get("minute") or 0), str(row.get("captured_at") or "")))
    history = history[-60:]
    _HISTORY[match_id] = history

    windows: dict[str, Any] = {}
    for window in (3, 5, 10, 15):
        previous = _previous_for_window(history, current, window)
        if previous is not None:
            windows[f"{window}m"] = _window_delta(current, previous, window)

    w5 = windows.get("5m")
    w10 = windows.get("10m")
    home_pressure, home_detail = _pressure_score(w5, "home")
    away_pressure, away_detail = _pressure_score(w5, "away")
    home_trend = _trend(w5, w10, "home")
    away_trend = _trend(w5, w10, "away")
    epoch = _epoch(history, current)
    state = _classify(home_pressure, away_pressure, home_trend, away_trend, epoch)
    period_samples = sum(1 for row in history if bool(row.get("usable")) and str(row.get("period")) == str(current.get("period")))

    out = {
        "version": 2,
        "mode": "active_memory",
        "snapshot_count": len(history),
        "period_snapshot_count": period_samples,
        "current": current,
        "score_epoch": epoch,
        "windows": windows,
        "pressure": {
            "state": state,
            "home": home_pressure,
            "away": away_pressure,
            "home_detail": home_detail,
            "away_detail": away_detail,
            "home_trend": home_trend,
            "away_trend": away_trend,
        },
    }
    record["brain_v3_memory"] = out
    return out


def _trace_path(now: datetime) -> Path:
    runtime = Path(os.getenv("RUNTIME_DATA_DIR", "data"))
    root = Path(os.getenv("GOOL_BRAIN_V3_TRACE_DIR", str(runtime / "live" / "brain_v3")))
    return root / now.strftime("%Y-%m-%d-%H.jsonl")


def _cleanup_trace(now: datetime) -> None:
    root = _trace_path(now).parent
    retention_hours = max(1.0, float(os.getenv("GOOL_BRAIN_V3_TRACE_RETENTION_HOURS", "24")))
    cutoff = time.time() - retention_hours * 3600.0
    try:
        for path in root.glob("*.jsonl"):
            try:
                if path.stat().st_mtime < cutoff:
                    path.unlink()
            except OSError:
                continue
    except OSError:
        return


def _append_trace(record: dict[str, Any], memory: dict[str, Any]) -> None:
    global _TRACE_WRITES
    raw = str(os.getenv("GOOL_BRAIN_V3_TRACE", "1")).strip().lower()
    if raw in {"0", "false", "no", "off"}:
        return
    now = datetime.now(timezone.utc)
    match = record.get("match") or {}
    payload = {
        "captured_at": record.get("captured_at"),
        "match_id": match.get("flashscore_event_id"),
        "home": match.get("home"),
        "away": match.get("away"),
        "league": match.get("league"),
        "minute": match.get("minute"),
        "score": [match.get("home_score"), match.get("away_score")],
        "brain_v3_memory": memory,
    }
    try:
        path = _trace_path(now)
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + "\n")
        _TRACE_WRITES += 1
        if _TRACE_WRITES % 120 == 0:
            _cleanup_trace(now)
    except Exception as exc:
        print(f"GOOL_BRAIN_V3_TRACE_ERROR {type(exc).__name__}:{exc}", flush=True)


def install_brain_v3_memory() -> None:
    global _INSTALLED
    if _INSTALLED:
        return

    from . import runtime_hardening as hardening

    original: Callable[[Any, dict[str, Any]], int] = hardening._safe_process

    def process_with_memory(self: Any, record: dict[str, Any]) -> int:
        match = record.get("match") or {}
        mid = str(match.get("flashscore_event_id") or "")
        replay = bool(record.get("runtime_market_recheck"))
        memory = None
        if mid and not replay and int(match.get("minute") or 0) > 0:
            try:
                memory = build_brain_v3_memory(record, mid)
                _append_trace(record, memory)
            except Exception as exc:
                print(f"GOOL_BRAIN_V3_MEMORY_ERROR match={mid} {type(exc).__name__}:{exc}", flush=True)
        try:
            return int(original(self, record) or 0)
        finally:
            if mid and bool(match.get("is_finished")):
                _HISTORY.pop(mid, None)

    hardening._safe_process = process_with_memory
    _INSTALLED = True
    print(
        "GOOL_BRAIN_V3_MEMORY installed snapshots=continuous windows=3/5/10/15m epochs=score+half mode=active_memory",
        flush=True,
    )


__all__ = ["build_brain_v3_memory", "install_brain_v3_memory"]
