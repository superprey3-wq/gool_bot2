from __future__ import annotations

import threading
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

try:
    import fcntl
except ImportError:  # pragma: no cover
    fcntl = None


_LOCK = threading.RLock()
_INSTALLED = False
_ORIGINALS: dict[str, Any] = {}


@contextmanager
def _locked(journal_path: Path) -> Iterator[None]:
    """Serialize journal read-modify-write transactions across threads/processes."""
    path = Path(journal_path)
    with _LOCK:
        lock_path = path.with_name(path.name + ".pipeline.lock")
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        handle = lock_path.open("a+", encoding="utf-8")
        try:
            if fcntl is not None:
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
            yield
        finally:
            if fcntl is not None:
                try:
                    fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
                except Exception:
                    pass
            handle.close()


def _entry_from_decision(*args: Any, **kwargs: Any) -> dict[str, Any] | None:
    """New active entries are explicitly private until Telegram accepts them."""
    row = _ORIGINALS["entry_from_decision"](*args, **kwargs)
    if isinstance(row, dict) and str(row.get("mode") or "").lower() == "active":
        row["telegram_sent"] = False
    return row


def _settle(record: dict[str, Any], journal_path: Path):
    with _locked(Path(journal_path)):
        return _ORIGINALS["settle"](record, journal_path)


def _record(record: dict[str, Any], decision: Any, experts: dict[str, Any], journal_path: Path, *, data_quality: float):
    with _locked(Path(journal_path)):
        return _ORIGINALS["record"](
            record,
            decision,
            experts,
            journal_path,
            data_quality=data_quality,
        )


def _persist_brain(row: dict[str, Any]) -> bool:
    from . import brain_journal_tracking as tracking

    path = tracking._journal_path()
    with _locked(path):
        return bool(_ORIGINALS["persist_brain"](row))


def _finalize_signal(journal_path: Path, entry: dict[str, Any] | None, sent: int) -> bool:
    with _locked(Path(journal_path)):
        return bool(_ORIGINALS["finalize_signal"](journal_path, entry, sent))


def _pending_results(
    journal_path: Path,
    *,
    match_id: str | None = None,
    origins: set[str] | None = None,
):
    path = Path(journal_path)
    with _locked(path):
        original = _ORIGINALS["pending_results"]
        try:
            return original(
                path,
                match_id=match_id,
                origins=origins,
            )
        except TypeError as exc:
            # Rolling deploy compatibility: an older stale-replay wrapper may
            # still expose the pre-origins signature. Do not let that break the
            # PREMATCH result watchdog.
            if "unexpected keyword argument 'origins'" not in str(exc):
                raise
            claimed = list(original(path, match_id=match_id) or [])
            if origins is None:
                return claimed

            wanted = {str(value).casefold() for value in origins}
            accepted = [
                row for row in claimed
                if str(row.get("origin") or "").casefold() in wanted
            ]
            rejected = [
                row for row in claimed
                if str(row.get("origin") or "").casefold() not in wanted
            ]
            if rejected:
                from . import multi_delivery as delivery
                from .journal import load_signal_journal, save_signal_journal

                rejected_claims = {
                    str(row.get("result_notification_claim_id") or "")
                    for row in rejected
                    if str(row.get("result_notification_claim_id") or "")
                }
                if rejected_claims:
                    with delivery._journal_delivery_lock(path):
                        rows = load_signal_journal(path)
                        dirty = False
                        for row in rows:
                            if str(row.get("result_notification_claim_id") or "") in rejected_claims:
                                delivery._clear_claim(row)
                                dirty = True
                        if dirty:
                            save_signal_journal(path, rows)
            return accepted


def _finalize_result(journal_path: Path, row: dict[str, Any], sent: int) -> bool:
    with _locked(Path(journal_path)):
        return bool(_ORIGINALS["finalize_result"](journal_path, row, sent))


def install_production_journal_serialization() -> None:
    """Put every public journal mutation behind one transaction lock."""
    global _INSTALLED
    if _INSTALLED:
        return
    with _LOCK:
        if _INSTALLED:
            return
        from . import brain_journal_tracking as tracking
        from . import multi_delivery as delivery
        from . import multi_journal as journal
        from . import multi_menu
        from . import multi_result_reconcile
        from . import multi_runtime
        from . import multi_telegram

        _ORIGINALS["entry_from_decision"] = journal.entry_from_decision
        _ORIGINALS["settle"] = journal.settle_multi_journal
        _ORIGINALS["record"] = journal.record_multi_entry
        _ORIGINALS["persist_brain"] = tracking._persist_tracking_row
        _ORIGINALS["finalize_signal"] = delivery.finalize_multi_delivery
        _ORIGINALS["pending_results"] = delivery.pending_result_notifications
        _ORIGINALS["finalize_result"] = delivery.finalize_result_delivery

        journal.entry_from_decision = _entry_from_decision
        journal.settle_multi_journal = _settle
        multi_runtime.settle_multi_journal = _settle
        multi_menu.settle_multi_journal = _settle
        multi_result_reconcile.settle_multi_journal = _settle

        journal.record_multi_entry = _record
        tracking._persist_tracking_row = _persist_brain

        delivery.finalize_multi_delivery = _finalize_signal
        multi_runtime.finalize_multi_delivery = _finalize_signal

        delivery.pending_result_notifications = _pending_results
        multi_runtime.pending_result_notifications = _pending_results

        delivery.finalize_result_delivery = _finalize_result
        multi_telegram.finalize_result_delivery = _finalize_result

        _INSTALLED = True
        print(
            "GOOL_JOURNAL_SERIALIZATION installed lock=pipeline "
            "read_modify_write=serialized new_entries=telegram_unsent_until_delivery",
            flush=True,
        )


__all__ = ["install_production_journal_serialization"]
