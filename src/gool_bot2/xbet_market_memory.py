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
_LAST_FINISHED_CHECK_AT = 0.0


def _runtime() -> Path:
    return Path(os.getenv("RUNTIME_DATA_DIR", "data"))


def memory_dir() -> Path:
    return Path(os.getenv("XBET_MARKET_MEMORY_DIR", str(_runtime() / "live" / "market_memory")))


def state_path() -> Path:
    return Path(os.getenv("XBET_MARKET_MEMORY_STATE", str(_runtime() / "live" / "market_memory_state.json")))


def _truthy(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return bool(default)
    return str(raw).strip().casefold() in {"1", "true", "yes", "on"}


def _safe_event_name(event_id: str) -> str:
    return "".join(ch for ch in str(event_id) if ch.isalnum() or ch in {"-", "_"}) or "unknown"


def _event_path(event_id: str) -> Path:
    return memory_dir() / "active" / f"{_safe_event_name(event_id)}.jsonl"


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


def _snapshot_ts(value: Any) -> float:
    try:
        dt = datetime.fromisoformat(str(value or "").replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.timestamp()
    except Exception:
        return time.time()


def _window_delta(points: list[dict[str, Any]], key: str, current: float, now_ts: float, seconds: float) -> dict[str, float] | None:
    target = now_ts - float(seconds)
    candidates = [p for p in points if float(p.get("ts") or 0.0) <= target and key in (p.get("markets") or {})]
    if not candidates:
        return None
    base = float((candidates[-1].get("markets") or {}).get(key))
    return {
        "from": round(base, 4),
        "to": round(float(current), 4),
        "odd_change": round(float(current) - base, 4),
        "implied_change_pp": round((_implied(float(current)) - _implied(base)) * 100.0, 2),
    }


def _update_summary(snapshot: dict[str, Any]) -> None:
    payload = _load_state()
    matches = payload.setdefault("matches", {})
    event_id = str(snapshot.get("event_id") or "")
    row = matches.get(event_id)
    if not isinstance(row, dict):
        row = {
            "event_id": event_id,
            "flashscore_event_id": snapshot.get("flashscore_event_id"),
            "home": snapshot.get("home"),
            "away": snapshot.get("away"),
            "scheduled_start_ts": snapshot.get("scheduled_start_ts"),
            "first_captured_at": snapshot.get("captured_at"),
            "opening": {},
            "points": [],
        }
    current = _flatten(snapshot.get("markets") or {})
    opening = row.setdefault("opening", {})
    previous_phase = str(row.get("phase") or "")
    incoming_phase = str(snapshot.get("phase") or "")
    if incoming_phase == "LIVE" and previous_phase == "PREMATCH" and not row.get("closing"):
        row["closing"] = dict(row.get("current") or {})
        row["closing_captured_at"] = row.get("last_captured_at")

    now_ts = _snapshot_ts(snapshot.get("captured_at"))
    points = [p for p in (row.get("points") or []) if isinstance(p, dict) and now_ts - float(p.get("ts") or 0.0) <= 2 * 3600]
    points.append({"ts": now_ts, "phase": incoming_phase, "markets": current})
    points = points[-240:]

    movement: dict[str, Any] = {}
    recent: dict[str, Any] = {}
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
        windows: dict[str, Any] = {}
        for label, seconds in (("5m", 300.0), ("15m", 900.0), ("60m", 3600.0)):
            delta = _window_delta(points[:-1], key, float(odd), now_ts, seconds)
            if delta is not None:
                windows[label] = delta
        if windows:
            recent[key] = windows

    row.update({
        "flashscore_event_id": snapshot.get("flashscore_event_id") or row.get("flashscore_event_id"),
        "home": snapshot.get("home"),
        "away": snapshot.get("away"),
        "scheduled_start_ts": snapshot.get("scheduled_start_ts") or row.get("scheduled_start_ts"),
        "last_captured_at": snapshot.get("captured_at"),
        "phase": incoming_phase,
        "minute": snapshot.get("minute"),
        "score_home": snapshot.get("score_home"),
        "score_away": snapshot.get("score_away"),
        "current": current,
        "movement": movement,
        "recent_movement": recent,
        "points": points,
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
    retention_days = max(1, int(os.getenv("XBET_MARKET_MEMORY_RETENTION_DAYS", "30")))
    cutoff = now - retention_days * 86400
    files = sorted(root.rglob("*.jsonl"))
    for path in list(files):
        try:
            if path.stat().st_mtime < cutoff:
                path.unlink()
        except OSError:
            pass
    cap = max(32 * 1024 * 1024, int(os.getenv("XBET_MARKET_MEMORY_MAX_BYTES", str(3 * 1024 * 1024 * 1024))))
    files = sorted(root.rglob("*.jsonl"), key=lambda p: p.stat().st_mtime)
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



def delete_market_memory(event_id: str) -> bool:
    """Delete one match's detailed memory in ephemeral (Monkey) mode."""
    event_id = str(event_id or "").strip()
    if not event_id:
        return False
    changed = False
    with _LOCK:
        path = _event_path(event_id)
        try:
            if path.exists():
                path.unlink()
                changed = True
        except OSError:
            pass
        payload = _load_state()
        matches = payload.get("matches") or {}
        if isinstance(matches, dict) and event_id in matches:
            matches.pop(event_id, None)
            payload["matches"] = matches
            payload["captured_at"] = datetime.now(timezone.utc).isoformat()
            _write_state(payload)
            changed = True
        for phase in ("PREMATCH", "LIVE"):
            _LAST_WRITE.pop(f"{phase}:{event_id}", None)
    if changed:
        print(f"XBET_MARKET_MEMORY_DELETE event={event_id} reason=finished", flush=True)
    return changed


def purge_finished_ephemeral(provider: Any, live_flashscore_ids: set[str] | None = None) -> int:
    """Confirm FINISHED with Flashscore, then remove Monkey's per-match odds history."""
    global _LAST_FINISHED_CHECK_AT
    if not _truthy("XBET_MARKET_MEMORY_EPHEMERAL", False):
        return 0
    now = time.time()
    interval = max(30.0, float(os.getenv("XBET_MARKET_MEMORY_FINISH_CHECK_SECONDS", "120")))
    if now - _LAST_FINISHED_CHECK_AT < interval:
        return 0
    _LAST_FINISHED_CHECK_AT = now
    live_ids = {str(x) for x in (live_flashscore_ids or set()) if str(x)}
    payload = _load_state()
    matches = payload.get("matches") or {}
    if not isinstance(matches, dict):
        return 0
    candidates: dict[str, str] = {}
    for event_id, row in matches.items():
        if not isinstance(row, dict) or str(row.get("phase") or "").upper() != "LIVE":
            continue
        fs_id = str(row.get("flashscore_event_id") or "").strip()
        if not fs_id or fs_id in live_ids:
            continue
        candidates[str(event_id)] = fs_id
    if not candidates:
        return 0
    try:
        states = provider.event_states(set(candidates.values())) or {}
    except Exception as exc:
        print(f"XBET_MARKET_MEMORY_FINISH_CHECK_ERROR {type(exc).__name__}:{exc}", flush=True)
        return 0
    deleted = 0
    for event_id, fs_id in candidates.items():
        state = states.get(fs_id) or {}
        if bool(state.get("is_finished")):
            deleted += int(delete_market_memory(event_id))
    return deleted


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
    flashscore_event_id: str | None = None,
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
        "flashscore_event_id": str(flashscore_event_id or "") or None,
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
        if _truthy("XBET_MARKET_MEMORY_EPHEMERAL", False):
            path = _event_path(event_id)
            path.parent.mkdir(parents=True, exist_ok=True)
        else:
            path = root / f"{local_day}.jsonl"
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(snapshot, ensure_ascii=False, separators=(",", ":")) + "\n")
        _LAST_WRITE[cache_key] = (now, signature)
        _update_summary(snapshot)
        _prune(now)
    return True


__all__ = [
    "compact_markets", "delete_market_memory", "memory_dir",
    "purge_finished_ephemeral", "record_market_snapshot", "state_path",
]
