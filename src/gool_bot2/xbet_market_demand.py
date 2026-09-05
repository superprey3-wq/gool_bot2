from __future__ import annotations

import json
import os
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any


_LOCK = threading.Lock()
_ALLOWED_STATES = {"PASS", "BORDERLINE"}


def _runtime() -> Path:
    return Path(os.getenv("RUNTIME_DATA_DIR", "data"))


def demand_path() -> Path:
    raw = os.getenv("XBET_MARKET_DEMAND_PATH", "").strip()
    return Path(raw) if raw else _runtime() / "live" / "xbet_market_demand.json"


def _ttl_seconds() -> float:
    try:
        return max(24.0, min(180.0, float(os.getenv("XBET_MARKET_DEMAND_TTL_SECONDS", "90"))))
    except (TypeError, ValueError):
        return 90.0


def _parse_dt(value: Any) -> datetime | None:
    raw = str(value or "").strip()
    if not raw:
        return None
    try:
        dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except Exception:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _load_raw(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text("utf-8"))
    except Exception:
        return {"version": 1, "matches": {}}
    if not isinstance(payload, dict):
        return {"version": 1, "matches": {}}
    matches = payload.get("matches")
    if not isinstance(matches, dict):
        payload["matches"] = {}
    return payload


def _atomic_write(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, separators=(",", ":")), "utf-8")
    tmp.replace(path)


def _strategy(minute: int) -> str | None:
    if 1 <= int(minute) <= 35:
        return "goal_before_ht"
    if 46 <= int(minute) <= 75:
        return "another_goal"
    return None


def load_active_demands(path: Path | None = None, *, now: datetime | None = None) -> dict[str, dict[str, Any]]:
    """Return only live, unexpired football-first requests for detailed 1xBet prices."""
    target = path or demand_path()
    now = now or datetime.now(timezone.utc)
    with _LOCK:
        payload = _load_raw(target)
        rows = payload.get("matches") or {}
        active: dict[str, dict[str, Any]] = {}
        changed = False
        for match_id, raw in list(rows.items()):
            if not isinstance(raw, dict):
                changed = True
                continue
            expires = _parse_dt(raw.get("expires_at"))
            if expires is None or expires <= now:
                changed = True
                continue
            active[str(match_id)] = dict(raw)
        if changed:
            payload["matches"] = active
            payload["updated_at"] = now.isoformat()
            try:
                _atomic_write(target, payload)
            except Exception:
                pass
        return active


def request_live_market(record: dict[str, Any], experts: dict[str, Any]) -> dict[str, Any] | None:
    """Queue a detailed 1xBet request only after football evidence is plausible.

    PASS and BORDERLINE are intentionally accepted: BORDERLINE is exactly where a
    fresh bookmaker price/pressure can confirm or reject the football idea. HARD_NO
    and NO_DATA never consume a detailed GetGameZip request for ordinary GOOL.
    Autonomous STEAM is maintained by a separate narrow watch lane in the market
    collector, so this demand file does not turn market-only steam into football.
    """
    match = record.get("match") or {}
    match_id = str(match.get("flashscore_event_id") or "").strip()
    try:
        minute = int(match.get("minute") or 0)
    except (TypeError, ValueError):
        minute = 0
    strategy = _strategy(minute)
    if not match_id or strategy is None or bool(match.get("is_finished")) or bool(match.get("is_halftime")):
        return None

    expert = experts.get(strategy)
    if not isinstance(expert, dict):
        return None
    state = str(expert.get("state") or ("PASS" if expert.get("passed") else "NO_DATA")).upper()
    if state not in _ALLOWED_STATES:
        return None
    try:
        probability = float(expert.get("probability"))
    except (TypeError, ValueError):
        return None
    if probability > 1.0:
        probability /= 100.0
    if not 0.0 <= probability <= 1.0:
        return None

    now = datetime.now(timezone.utc)
    row = {
        "match_id": match_id,
        "home": match.get("home"),
        "away": match.get("away"),
        "league": match.get("league"),
        "minute": minute,
        "score": [int(match.get("home_score") or 0), int(match.get("away_score") or 0)],
        "strategy": strategy,
        "football_state": state,
        "football_probability": round(probability, 4),
        "requested_at": now.isoformat(),
        "expires_at": (now + timedelta(seconds=_ttl_seconds())).isoformat(),
    }
    target = demand_path()
    with _LOCK:
        payload = _load_raw(target)
        rows = payload.get("matches") or {}
        # Opportunistically trim stale entries while touching the file.
        clean: dict[str, Any] = {}
        for key, value in rows.items():
            if not isinstance(value, dict):
                continue
            expiry = _parse_dt(value.get("expires_at"))
            if expiry is not None and expiry > now:
                clean[str(key)] = value
        clean[match_id] = row
        payload.update({"version": 1, "updated_at": now.isoformat(), "matches": clean})
        try:
            _atomic_write(target, payload)
        except Exception as exc:
            print(f"XBET_DEMAND_WRITE_ERROR {type(exc).__name__}:{exc}", flush=True)
            return None
    return row


__all__ = ["demand_path", "load_active_demands", "request_live_market"]
