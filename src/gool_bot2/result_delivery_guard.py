from __future__ import annotations

import threading
from pathlib import Path
from typing import Any

from .result_delivery_once import finalize_result_reservation, reserve_result_delivery


_LOCK = threading.RLock()
_INSTALLED = False
_ORIGINAL_EMIT: Any = None


def _guarded_emit(record: dict[str, Any], rows: list[dict[str, Any]], *, journal_path: Path | None = None) -> int:
    """Final at-most-once barrier for every GOOL result-card path.

    The guard is deliberately installed on ``multi_telegram.emit_multi_results``
    itself, not only on a runtime alias. LIVE settlement, menu reconciliation and
    Brain retry code therefore all hit this exact function before Telegram.
    """
    if not rows:
        return 0
    if journal_path is None or _ORIGINAL_EMIT is None:
        return int(_ORIGINAL_EMIT(record, rows, journal_path=journal_path) or 0) if _ORIGINAL_EMIT else 0

    total = 0
    for row in rows:
        token = reserve_result_delivery(Path(journal_path), row)
        if token is None:
            print(
                f"GOOL_RESULT_DUPLICATE_BLOCKED match={row.get('match_id')} "
                f"entry={row.get('entry_key') or row.get('brain_signal_key')} result={row.get('result')}",
                flush=True,
            )
            continue

        try:
            sent = int(_ORIGINAL_EMIT(record, [row], journal_path=journal_path) or 0)
        except Exception:
            finalize_result_reservation(Path(journal_path), row, token, 0)
            raise

        finalize_result_reservation(Path(journal_path), row, token, sent)
        total += sent
    return total


def install_result_delivery_guard() -> None:
    """Install one result sender for LIVE, menu and retry paths."""
    global _INSTALLED, _ORIGINAL_EMIT
    if _INSTALLED:
        return
    with _LOCK:
        if _INSTALLED:
            return
        from . import multi_runtime
        from . import multi_telegram

        # Capture the real renderer/sender once. All callers, including the
        # Brain journal retry path which imports multi_telegram dynamically, are
        # redirected through the same durable sidecar reservation.
        _ORIGINAL_EMIT = multi_telegram.emit_multi_results
        multi_telegram.emit_multi_results = _guarded_emit
        multi_runtime.emit_multi_results = _guarded_emit
        _INSTALLED = True
        print("GOOL_RESULT_DELIVERY_GUARD installed mode=at_most_once final_sender=multi_telegram", flush=True)


__all__ = ["install_result_delivery_guard"]
