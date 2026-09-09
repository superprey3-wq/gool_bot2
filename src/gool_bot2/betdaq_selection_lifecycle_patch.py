from __future__ import annotations

import os
import threading
from typing import Any, Callable


_LOCK = threading.Lock()
_INSTALLED = False
_ORIGINAL: Callable[[dict[str, Any]], list[dict[str, Any]]] | None = None


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


def process_with_lifecycle(state: dict[str, Any]) -> list[dict[str, Any]]:
    from .betdaq_selection_lifecycle import reconcile_pending, record_alerts

    try:
        settled = reconcile_pending()
        if settled:
            print(f"BETDAQ_SELECTION_RESULT settled={len(settled)}", flush=True)
        _notify_results()
    except Exception as exc:
        print(f"BETDAQ_SELECTION_RESULT reconcile_error={type(exc).__name__}:{exc}", flush=True)

    if _ORIGINAL is None:
        return []
    alerts = _ORIGINAL(state)
    if alerts:
        try:
            stored = record_alerts(alerts)
            print(f"BETDAQ_SELECTION_RESULT journaled={len(stored)} pending=1", flush=True)
        except Exception as exc:
            print(f"BETDAQ_SELECTION_RESULT record_error={type(exc).__name__}:{exc}", flush=True)
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
        print("BETDAQ_SELECTION_RESULT lifecycle=on settlement=flashscore", flush=True)


__all__ = ["install_betdaq_selection_lifecycle", "process_with_lifecycle"]
