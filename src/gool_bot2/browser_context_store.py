from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any


_CACHE_MTIME_NS = -1
_CACHE_PAYLOAD: dict[str, Any] = {}


def _path() -> Path:
    runtime = Path(os.getenv("RUNTIME_DATA_DIR", "data"))
    return Path(os.getenv("GOOL_BROWSER_CONTEXT_PATH", str(runtime / "live" / "browser_context.json")))


def _load() -> dict[str, Any]:
    global _CACHE_MTIME_NS, _CACHE_PAYLOAD
    path = _path()
    try:
        stat = path.stat()
    except OSError:
        return {}
    if int(stat.st_mtime_ns) == _CACHE_MTIME_NS:
        return _CACHE_PAYLOAD
    try:
        payload = json.loads(path.read_text("utf-8"))
    except Exception:
        return {}
    if not isinstance(payload, dict):
        return {}
    _CACHE_MTIME_NS = int(stat.st_mtime_ns)
    _CACHE_PAYLOAD = payload
    return payload


def _epoch(value: Any) -> float | None:
    try:
        if value in (None, ""):
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def context_for_record(record: dict[str, Any]) -> dict[str, Any] | None:
    match = record.get("match") or {}
    match_id = str(match.get("flashscore_event_id") or "").strip()
    if not match_id:
        return None
    payload = _load()
    contexts = payload.get("matches") or {}
    row = contexts.get(match_id) if isinstance(contexts, dict) else None
    if not isinstance(row, dict):
        return None

    captured = _epoch(row.get("captured_epoch"))
    ttl = max(30.0, float(os.getenv("GOOL_BROWSER_CONTEXT_TTL_SECONDS", "180")))
    if captured is None or time.time() - captured > ttl:
        return None

    expected_score = [int(match.get("home_score") or 0), int(match.get("away_score") or 0)]
    score = row.get("score")
    if isinstance(score, (list, tuple)) and len(score) >= 2:
        try:
            browser_score = [int(score[0]), int(score[1])]
        except (TypeError, ValueError):
            return None
        if browser_score != expected_score:
            return None

    try:
        browser_minute = int(row.get("minute") or 0)
        current_minute = int(match.get("minute") or 0)
    except (TypeError, ValueError):
        return None
    max_lag = max(1, int(os.getenv("GOOL_BROWSER_MAX_MINUTE_LAG", "3")))
    if browser_minute > 0 and current_minute > 0 and abs(browser_minute - current_minute) > max_lag:
        return None
    return dict(row)


def attach_browser_context(record: dict[str, Any]) -> dict[str, Any] | None:
    """Attach fresh browser evidence without double-weighting normal providers.

    match_context.provider_pair() treats browser365 as a fallback provider. It is
    considered only when fewer than two ordinary providers expose that statistic.
    """
    row = context_for_record(record)
    if row is None:
        return None
    record["browser_context"] = row
    stats = row.get("stats") or {}
    if isinstance(stats, dict) and stats:
        providers = record.setdefault("providers", {})
        if isinstance(providers, dict):
            providers["browser365"] = {
                "id": str(row.get("scores365_game_id") or ""),
                "stats": dict(stats),
                "meta": {
                    "source": "playwright_chromium",
                    "captured_epoch": row.get("captured_epoch"),
                    "minute": row.get("minute"),
                    "score": row.get("score"),
                    "page_url": row.get("page_url"),
                    "trend_count": len(row.get("trends") or []),
                },
            }
    return row


def browser_health() -> dict[str, Any]:
    payload = _load()
    health = payload.get("health") or {}
    return dict(health) if isinstance(health, dict) else {}


__all__ = ["context_for_record", "attach_browser_context", "browser_health"]
