from __future__ import annotations

import threading
from pathlib import Path
from typing import Any

from .result_delivery_once import finalize_result_reservation, reserve_result_delivery


_LOCK = threading.RLock()
_INSTALLED = False
_ORIGINAL_EMIT: Any = None


def _canonical_journal_path(journal_path: Path | None) -> Path:
    if journal_path is not None:
        return Path(journal_path)
    from .multi_menu import journal_path as multi_journal_path

    return multi_journal_path()


def _guarded_emit(
    record: dict[str, Any],
    rows: list[dict[str, Any]],
    *,
    journal_path: Path | None = None,
) -> int:
    """Final at-most-once barrier shared by LIVE and menu result delivery."""
    if not rows or _ORIGINAL_EMIT is None:
        return 0

    path = _canonical_journal_path(journal_path)
    total = 0
    for row in rows:
        token = reserve_result_delivery(path, row)
        if token is None:
            print(
                f"GOOL_RESULT_DUPLICATE_BLOCKED match={row.get('match_id')} "
                f"entry={row.get('entry_key') or row.get('brain_signal_key')} result={row.get('result')}",
                flush=True,
            )
            continue

        try:
            sent = int(_ORIGINAL_EMIT(record, [row], journal_path=path) or 0)
        except Exception:
            finalize_result_reservation(path, row, token, 0)
            raise

        finalize_result_reservation(path, row, token, sent)
        total += sent
    return total


def install_result_delivery_guard() -> None:
    """Route every production result-card path through the same durable guard."""
    global _INSTALLED, _ORIGINAL_EMIT
    if _INSTALLED:
        return
    with _LOCK:
        if _INSTALLED:
            return
        from . import multi_runtime
        from . import multi_telegram

        # multi_runtime imported emit_multi_results by value, while menu/Brain
        # reconciliation imports it dynamically from multi_telegram. Patch BOTH
        # bindings or one path can bypass the sidecar reservation and resend the
        # same WON/LOST card.
        _ORIGINAL_EMIT = multi_telegram.emit_multi_results
        multi_telegram.emit_multi_results = _guarded_emit
        multi_runtime.emit_multi_results = _guarded_emit

        _INSTALLED = True
        print(
            "GOOL_RESULT_DELIVERY_GUARD installed mode=at_most_once "
            "paths=live+menu+brain+steam sidecar=on",
            flush=True,
        )


__all__ = ["install_result_delivery_guard"]
