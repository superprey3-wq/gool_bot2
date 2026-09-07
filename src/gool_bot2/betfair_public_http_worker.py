from __future__ import annotations

import os
import threading
import time
from typing import Any
from urllib.request import Request, urlopen

from .betfair_public_board import (
    BETFAIR_FOOTBALL_URL,
    BETFAIR_INPLAY_URL,
    attach_price_flow,
    load_betfair_state,
    parse_betfair_html,
    save_betfair_state,
)

_STARTED = False
_LOCK = threading.Lock()


def _now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _fetch(url: str) -> tuple[int, str]:
    req = Request(
        url,
        headers={
            "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/140 Safari/537.36",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Encoding": "identity",
            "Referer": "https://www.betfair.com/",
        },
    )
    with urlopen(req, timeout=20) as response:
        return int(getattr(response, "status", 200) or 200), response.read().decode("utf-8", errors="ignore")


def collect_once() -> dict[str, Any]:
    previous = load_betfair_state()
    previous_rows = {
        str(row.get("event_key") or ""): row
        for row in (previous.get("events") or [])
        if isinstance(row, dict) and row.get("event_key")
    }
    merged: dict[str, dict[str, Any]] = {}
    statuses: list[str] = []
    errors: list[str] = []

    for url, force_live in ((BETFAIR_FOOTBALL_URL, False), (BETFAIR_INPLAY_URL, True)):
        try:
            status, body = _fetch(url)
            statuses.append(f"{status}:{'inplay' if force_live else 'all'}")
            rows = parse_betfair_html(body, force_live=force_live)
        except Exception as exc:
            errors.append(f"{type(exc).__name__}:{exc}")
            rows = []
        for row in rows:
            key = str(row.get("event_key") or "")
            old = merged.get(key)
            if old is None or float(row.get("matched_gbp") or 0.0) >= float(old.get("matched_gbp") or 0.0):
                merged[key] = row
            elif row.get("in_running"):
                old["in_running"] = True
                old["start_label"] = "LIVE"

    events = [attach_price_flow(previous_rows.get(key), row) for key, row in merged.items()]
    events.sort(key=lambda row: float(row.get("matched_gbp") or 0.0), reverse=True)
    payload = {
        "captured_epoch": time.time(),
        "captured_at": _now_iso(),
        "source": "betfair_public_http",
        "available": bool(events),
        "events": events,
        "statuses": statuses,
        "error": "; ".join(errors)[:1000] if errors else "",
    }
    save_betfair_state(payload)
    return payload


def _loop() -> None:
    interval = max(15.0, float(os.getenv("BETFAIR_PUBLIC_INTERVAL_SECONDS", "30")))
    print(f"BETFAIR_PUBLIC started mode=http interval={interval:.0f}s auth=none", flush=True)
    while True:
        started = time.monotonic()
        try:
            state = collect_once()
            top = max((float(row.get("matched_gbp") or 0.0) for row in state.get("events") or []), default=0.0)
            print(
                f"BETFAIR_PUBLIC captured={len(state.get('events') or [])} top_matched_gbp={top:.0f} "
                f"available={int(bool(state.get('available')))} statuses={','.join(state.get('statuses') or [])} "
                f"error={str(state.get('error') or '')[:160]}",
                flush=True,
            )
        except Exception as exc:
            print(f"BETFAIR_PUBLIC error={type(exc).__name__}:{exc}", flush=True)
        elapsed = time.monotonic() - started
        time.sleep(max(2.0, interval - elapsed))


def start_background_worker() -> bool:
    global _STARTED
    if str(os.getenv("BETFAIR_PUBLIC_ENABLE", "1")).strip().lower() in {"0", "false", "no", "off"}:
        print("BETFAIR_PUBLIC disabled reason=config", flush=True)
        return False
    with _LOCK:
        if _STARTED:
            return True
        thread = threading.Thread(target=_loop, name="gool-betfair-public", daemon=True)
        thread.start()
        _STARTED = True
    return True


__all__ = ["collect_once", "start_background_worker"]
