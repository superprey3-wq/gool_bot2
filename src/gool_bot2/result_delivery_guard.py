from __future__ import annotations

import threading
from pathlib import Path
from typing import Any

from .result_delivery_once import finalize_result_reservation, reserve_result_delivery


_LOCK = threading.RLock()
_INSTALLED = False
_ORIGINAL_EMIT: Any = None


def _guarded_emit(record: dict[str, Any], rows: list[dict[str, Any]], *, journal_path: Path | None = None) -> int:
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
    global _INSTALLED, _ORIGINAL_EMIT
    if _INSTALLED:
        return
    with _LOCK:
        if _INSTALLED:
            return
        from . import brain_journal_results
        from . import multi_runtime

        _ORIGINAL_EMIT = multi_runtime.emit_multi_results
        multi_runtime.emit_multi_results = _guarded_emit
        # brain_journal_results._menu_reconcile resolves this module global
        # dynamically, so patch it too. LIVE settlement and menu reconciliation
        # therefore hit the same final sidecar reservation before Telegram.
        brain_journal_results._emit_results = _guarded_emit
        _INSTALLED = True
        print("GOOL_RESULT_DELIVERY_GUARD installed mode=at_most_once sidecar=on", flush=True)


__all__ = ["install_result_delivery_guard"]
