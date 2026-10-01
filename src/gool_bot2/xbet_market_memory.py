from __future__ import annotations

import json
import os
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo


_MOSCOW = ZoneInfo("Europe/Moscow")
_LOCK = threading.Lock()
_LAST_WRITE: dict[str, tuple[float, str]] = {}
_LAST_PRUNE_AT = 0.0


def _runtime() -> Path:
    return Path(os.getenv("RUNTIME_DATA_DIR", "data"))


def memory_dir() -> Path:
    return Path(os.getenv("XBET_MARKET_MEMORY_DIR", str(_runtime() / "live" / "market_memory")))


def state_path() -> Path:
    return Path(os.getenv("XBET_MARKET_MEMORY_STATE", str(_runtime() / "live" / "market_memory_state.json")))


def _number(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if number > 1.0 else None


def _clean_pair(value: Any) -> dict[str, float]:
    if not isinstance(value, dict):
        return {}
    out: dict[str, float] = {}
    for key, raw in value.items():
        number = _number(raw)
        if number is not None:
            out[str(key)] = round(number, 5)
    return out


def _clean_rows(value: Any) -> list[dict[str, float]]:
    rows: list[dict[str, float]] = []
    for raw in value or []:
        if not isinstance(raw, dict):
            continue
        try:
            line = float(raw.get("line"))
        except (TypeError, ValueError):
            continue
        row: dict[str, float] = {"line": line}
        for key in ("over", "under"):
            number = _number(raw.get(key))
            if number is not None:
                row[key] = round(number, 5)
        if len(row) > 1:
            rows.append(row)
    return rows


def compact_markets(markets: dict[str, Any] | None) -> dict[str, Any]:
    markets = markets or {}
    return {
        "match_1x2": _clean_pair(markets.get("match_1x2")),
        "btts": _clean_pair(markets.get("btts")),
        "match_total": _clean_rows(markets.get("match_total")),
        "home_total": _clean_rows(markets.get("home_total")),
        "away_total": _clean_rows(markets.get("away_total")),
        "first_half_total": _clean_rows(markets.get("first_half_total")),
    }


def _flatten(markets: dict[str, Any]) -> dict[str, float]:
    out: dict[str, float] = {}
    for family in ("match_1x2", "btts"):
        for selection, odd in (markets.get(family) or {}).items():
            number = _number(odd)
            if number is not None:
                out[f"{family}:{selection}"] = number
    for family in ("match_total", "home_total", "away_total", "first_half_total"):
        for row in markets.get(family) or []:
            if not isinstance(row, dict):
                continue
            try:
                line = float(row.get("line"))
            except (TypeError, ValueError):
                continue
            for side in ("over", "under"):
                number = _number(row.get(side))
                if number is not None:
                    out[f"{family}:{line:g}:{side}"] = number
    return out


def _implied(odd: float) -> float:
    return 1.0 / max(1.000001, float(odd))


def _load_state() -> dict[str, Any]:
    try:
        payload = json.loads(state_path().read_text("utf-8"))
        return payload if isinstance(payload, dict) else {}
    except Exception:
        return {}


def _write_state(payload: dict[str, Any]) -> None:
    path = state_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, separators=(",", ":")), "utf-8")
    tmp.replace(path)


def _update_summary(snapshot: dict[str, Any]) -> None:
    payload = _load_state()
    matches = payload.setdefault("matches", {})
    event_id = str(snapshot.get("event_id") or "")
    row = matches.get(event_id)
    if not isinstance(row, dict):
        row = {
            "event_id": event_id,
            "home": snapshot.get("home"),
            "away": snapshot.get("away"),
            "scheduled_start_ts": snapshot.get("scheduled_start_ts"),
            "first_captured_at": snapshot.get("captured_at"),
            "opening": {},
        }
    current = _flatten(snapshot.get("markets") or {})
    opening = row.setdefault("opening", {})
    movement: dict[str, Any] = {}
    for key, odd in current.items():
        if key not in opening:
            opening[key] = round(float(odd), 5)
        open_odd = float(opening[key])
        movement[key] = {
            "open": round(open_odd, 4),
            "current": round(float(odd), 4),
            "odd_change": round(float(odd) - open_odd, 4),
            "implied_change_pp": round((_implied(float(odd)) - _implied(open_odd)) * 100.0, 2),
        }
    row.update({
        "home": snapshot.get("home"),
        "away": snapshot.get("away"),
        "scheduled_start_ts": snapshot.get("scheduled_start_ts") or row.get("scheduled_start_ts"),
        "last_captured_at": snapshot.get("captured_at"),
        "phase": snapshot.get("phase"),
        "minute": snapshot.get("minute"),
        "score_home": snapshot.get("score_home"),
        "score_away": snapshot.get("score_away"),
        "current": current,
        "movement": movement,
    })
    matches[event_id] = row
    payload["captured_at"] = snapshot.get("captured_at")
    _write_state(payload)


def _prune(now: float) -> None:
    global _LAST_PRUNE_AT
    if now - _LAST_PRUNE_AT < 600:
        return
    _LAST_PRUNE_AT = now
    root = memory_dir()
    if not root.exists():
        return
    retention_days = max(2, int(os.getenv("XBET_MARKET_MEMORY_RETENTION_DAYS", "30")))
    cutoff = now - retention_days * 86400
    files = sorted(root.glob("*.jsonl"))
    for path in list(files):
        try:
            if path.stat().st_mtime < cutoff:
                path.unlink()
        except OSError:
            pass
    cap = max(256 * 1024 * 1024, int(os.getenv("XBET_MARKET_MEMORY_MAX_BYTES", str(3 * 1024 * 1024 * 1024))))
    files = sorted(root.glob("*.jsonl"), key=lambda p: p.stat().st_mtime)
    total = 0
    sizes: list[tuple[Path, int]] = []
    for path in files:
        try:
            size = path.stat().st_size
        except OSError:
            continue
        sizes.append((path, size))
        total += size
    for path, size in sizes:
        if total <= cap or len(sizes) <= 1:
            break
        try:
            path.unlink()
            total -= size
        except OSError:
            pass


def record_market_snapshot(
    *,
    event_id: str,
    home: str,
    away: str,
    phase: str,
    markets: dict[str, Any] | None,
    captured_at: str | None = None,
    scheduled_start_ts: float | None = None,
    minute: int | None = None,
    score_home: int | None = None,
    score_away: int | None = None,
) -> bool:
    event_id = str(event_id or "").strip()
    cleaned = compact_markets(markets)
    if not event_id or not any(cleaned.values()):
        return False
    now = time.time()
    heartbeat = float(os.getenv(
        "XBET_MARKET_MEMORY_LIVE_HEARTBEAT_SECONDS" if str(phase).upper() == "LIVE" else "XBET_MARKET_MEMORY_PREMATCH_HEARTBEAT_SECONDS",
        "60" if str(phase).upper() == "LIVE" else "300",
    ))
    signature = json.dumps(cleaned, sort_keys=True, separators=(",", ":"))
    cache_key = f"{str(phase).upper()}:{event_id}"
    previous = _LAST_WRITE.get(cache_key)
    if previous and previous[1] == signature and now - previous[0] < max(5.0, heartbeat):
        return False

    captured = captured_at or datetime.now(timezone.utc).isoformat()
    local_day = datetime.now(timezone.utc).astimezone(_MOSCOW).strftime("%Y-%m-%d")
    snapshot = {
        "captured_at": captured,
        "moscow_day": local_day,
        "phase": str(phase).upper(),
        "event_id": event_id,
        "home": str(home or ""),
        "away": str(away or ""),
        "scheduled_start_ts": scheduled_start_ts,
        "minute": minute,
        "score_home": score_home,
        "score_away": score_away,
        "markets": cleaned,
    }
    with _LOCK:
        root = memory_dir()
        root.mkdir(parents=True, exist_ok=True)
        path = root / f"{local_day}.jsonl"
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(snapshot, ensure_ascii=False, separators=(",", ":")) + "\n")
        _LAST_WRITE[cache_key] = (now, signature)
        _update_summary(snapshot)
        _prune(now)
    return True


__all__ = ["compact_markets", "memory_dir", "record_market_snapshot", "state_path"]
