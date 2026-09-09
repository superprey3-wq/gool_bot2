from __future__ import annotations

import os
import threading
from typing import Any, Callable


_LOCK = threading.Lock()
_INSTALLED = False
_ORIGINAL: Callable[[dict[str, Any]], list[dict[str, Any]]] | None = None
_WORKER_RUNNING = False
_PENDING_ALERTS: list[dict[str, Any]] = []


def _active() -> bool:
    return str(os.getenv("GOOL_MULTI_TELEGRAM_MODE", "shadow")).strip().lower() == "active"


def _notify_results() -> None:
    if not _active():
        return
    from . import telegram
    from .betdaq_selection_lifecycle import mark_notified, pending_notifications, result_text

    sent_ids: list[str] = []
    for row in pending_notifications():
        try:
            sent = telegram.broadcast(result_text(row))
        except Exception as exc:
            print(f"BETDAQ_SELECTION_RESULT telegram_error={type(exc).__name__}:{exc}", flush=True)
            sent = 0
        if sent > 0:
            sent_ids.append(str(row.get("signal_id") or ""))
    if sent_ids:
        mark_notified(sent_ids)


def _background_worker() -> None:
    global _WORKER_RUNNING
    from .betdaq_selection_lifecycle import reconcile_pending, record_alerts

    while True:
        with _LOCK:
            queued = [dict(row) for row in _PENDING_ALERTS]
            _PENDING_ALERTS.clear()
        try:
            if queued:
                stored = record_alerts(queued)
                print(f"BETDAQ_SELECTION_RESULT journaled={len(stored)} pending=1", flush=True)
            settled = reconcile_pending(force=bool(queued))
            if settled:
                print(f"BETDAQ_SELECTION_RESULT settled={len(settled)}", flush=True)
            _notify_results()
        except Exception as exc:
            print(f"BETDAQ_SELECTION_RESULT background_error={type(exc).__name__}:{exc}", flush=True)

        with _LOCK:
            if _PENDING_ALERTS:
                continue
            _WORKER_RUNNING = False
            return


def _kick_background(alerts: list[dict[str, Any]] | None = None) -> None:
    global _WORKER_RUNNING
    with _LOCK:
        for row in alerts or []:
            _PENDING_ALERTS.append(dict(row))
        if _WORKER_RUNNING:
            return
        _WORKER_RUNNING = True
    threading.Thread(target=_background_worker, daemon=True, name="betdaq-selection-results").start()


def process_with_lifecycle(state: dict[str, Any]) -> list[dict[str, Any]]:
    """Keep detector latency independent from Flashscore mapping/settlement I/O."""
    if _ORIGINAL is None:
        return []
    alerts = _ORIGINAL(state)
    _kick_background(alerts)
    return alerts


def install_betdaq_selection_lifecycle() -> None:
    global _INSTALLED, _ORIGINAL
    if _INSTALLED:
        return
    with _LOCK:
        if _INSTALLED:
            return
        from . import betdaq_selection_alerts as alerts

        _ORIGINAL = alerts.process_selection_alerts
        alerts.process_selection_alerts = process_with_lifecycle
        _INSTALLED = True
        print("BETDAQ_SELECTION_RESULT lifecycle=on settlement=flashscore async=1", flush=True)


__all__ = ["install_betdaq_selection_lifecycle", "process_with_lifecycle"]
