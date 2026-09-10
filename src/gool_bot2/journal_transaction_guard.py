from __future__ import annotations

import threading
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

from .journal import journal_transaction


_LOCK = threading.RLock()
_INSTALLED = False
_ORIGINALS: dict[str, Any] = {}


def _settle(record: dict[str, Any], journal_path: Path):
    with journal_transaction(Path(journal_path)):
        return _ORIGINALS["settle"](record, journal_path)


def _record(record, decision, experts, journal_path: Path, *, data_quality: float):
    with journal_transaction(Path(journal_path)):
        return _ORIGINALS["record"](
            record,
            decision,
            experts,
            journal_path,
            data_quality=data_quality,
        )


def _sync(record, decision, experts, journal_path: Path, *, data_quality: float):
    with journal_transaction(Path(journal_path)):
        return _ORIGINALS["sync"](
            record,
            decision,
            experts,
            journal_path,
            data_quality=data_quality,
        )


def _persist_brain(row: dict[str, Any]) -> bool:
    from . import brain_journal_tracking as tracking

    with journal_transaction(tracking._journal_path()):
        return bool(_ORIGINALS["persist_brain"](row))


def _finalize_signal(journal_path: Path, entry: dict[str, Any] | None, sent: int) -> bool:
    with journal_transaction(Path(journal_path)):
        return bool(_ORIGINALS["finalize_signal"](journal_path, entry, sent))


@contextmanager
def _delivery_lock(journal_path: Path) -> Iterator[None]:
    """Use one lock order for journal state and result-delivery file lock.

    Result claiming used a separate delivery lock while settlement used no
    transaction lock. The menu thread could therefore save an older journal
    snapshot over a LIVE settlement/claim. Always acquire journal transaction
    first, then the existing cross-process delivery lock.
    """
    with journal_transaction(Path(journal_path)):
        with _ORIGINALS["delivery_lock"](journal_path):
            yield


def install_journal_transaction_guard() -> None:
    global _INSTALLED
    if _INSTALLED:
        return
    with _LOCK:
        if _INSTALLED:
            return
        from . import brain_journal_tracking as tracking
        from . import multi_delivery as delivery
        from . import multi_journal as journal
        from . import multi_result_reconcile as result_reconcile
        from . import multi_runtime as runtime

        _ORIGINALS.update(
            {
                "settle": journal.settle_multi_journal,
                "record": journal.record_multi_entry,
                "sync": journal.sync_multi_journal,
                "persist_brain": tracking._persist_tracking_row,
                "finalize_signal": delivery.finalize_multi_delivery,
                "delivery_lock": delivery._journal_delivery_lock,
            }
        )

        journal.settle_multi_journal = _settle
        journal.record_multi_entry = _record
        journal.sync_multi_journal = _sync
        tracking._persist_tracking_row = _persist_brain
        delivery.finalize_multi_delivery = _finalize_signal
        delivery._journal_delivery_lock = _delivery_lock

        # These modules imported the functions by value before product startup.
        runtime.settle_multi_journal = _settle
        runtime.sync_multi_journal = _sync
        runtime.finalize_multi_delivery = _finalize_signal
        result_reconcile.settle_multi_journal = _settle

        _INSTALLED = True
        print(
            "GOOL_JOURNAL_TX installed live_thread=serialized menu_thread=serialized "
            "delivery_lock_order=journal_then_result",
            flush=True,
        )


__all__ = ["install_journal_transaction_guard"]
