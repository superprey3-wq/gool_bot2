from __future__ import annotations

import os
import threading
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

from .journal import load_signal_journal, save_signal_journal

try:
    import fcntl
except ImportError:  # pragma: no cover - production is Linux; thread lock remains as fallback.
    fcntl = None


FINAL_RESULTS = {"won", "lost", "push", "void"}
_DELIVERY_LOCK = threading.RLock()
_CLAIM_FIELDS = (
    "result_notification_claim_id",
    "result_notification_claimed_at",
    "result_notification_claim_version",
)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _parse_dt(value: Any) -> datetime | None:
    raw = str(value or "").strip()
    if not raw:
        return None
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except Exception:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _claim_ttl_seconds() -> float:
    try:
        return max(30.0, float(os.getenv("GOOL_RESULT_NOTIFICATION_CLAIM_TTL_SECONDS", "180")))
    except (TypeError, ValueError):
        return 180.0


def _notification_version(row: dict[str, Any]) -> str:
    return str(
        row.get("result_notification_created_at")
        or row.get("settled_at")
        or f"{row.get('result')}:{row.get('settled_minute')}:{row.get('settled_score')}"
    )


def _claim_is_active(row: dict[str, Any], now: datetime) -> bool:
    claim_id = str(row.get("result_notification_claim_id") or "")
    claimed_at = _parse_dt(row.get("result_notification_claimed_at"))
    if not claim_id or claimed_at is None:
        return False
    if str(row.get("result_notification_claim_version") or "") != _notification_version(row):
        return False
    age = (now - claimed_at.astimezone(timezone.utc)).total_seconds()
    return 0.0 <= age < _claim_ttl_seconds()


def _clear_claim(row: dict[str, Any]) -> None:
    for key in _CLAIM_FIELDS:
        row.pop(key, None)


@contextmanager
def _journal_delivery_lock(journal_path: Path) -> Iterator[None]:
    """Serialize notification claims across threads and Linux worker processes."""
    with _DELIVERY_LOCK:
        lock_path = journal_path.with_name(journal_path.name + ".delivery.lock")
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


def finalize_multi_delivery(
    journal_path: Path,
    entry: dict[str, Any] | None,
    sent: int,
) -> bool:
    """Keep active journal/bank aligned with signals actually delivered to Telegram.

    Shadow entries are untouched. For active mode a failed Telegram delivery is
    removed immediately, so it cannot later settle as a bet the user never saw.
    """
    if not entry or str(entry.get("mode") or "").lower() != "active":
        return False
    entry_key = str(entry.get("entry_key") or "")
    if not entry_key:
        return False

    rows = load_signal_journal(journal_path)
    index = next(
        (i for i, row in enumerate(rows) if str(row.get("entry_key") or "") == entry_key),
        None,
    )
    if index is None:
        return False

    if int(sent or 0) > 0:
        rows[index]["telegram_sent"] = True
        rows[index]["telegram_sent_at"] = _now()
        rows[index]["telegram_delivery_count"] = int(sent)
        if str(rows[index].get("source") or "").startswith("1xbet:autonomous_steam"):
            rows[index]["signal_source"] = "STEAM_OVERRIDE"
    else:
        rows.pop(index)

    save_signal_journal(journal_path, rows)
    return True


def was_publicly_sent(row: dict[str, Any]) -> bool:
    """Whether a settled row is allowed to produce a public result card.

    New entries carry an explicit delivery flag. Legacy active rows predate the
    flag, so the old 70% public-card gate is used only as a compatibility rule:
    rows that would have been suppressed cannot suddenly emit a result.
    """
    if "telegram_sent" in row:
        return bool(row.get("telegram_sent"))
    if str(row.get("mode") or "").lower() != "active":
        return False
    try:
        return float(row.get("probability")) >= 0.70
    except (TypeError, ValueError):
        return False


def pending_result_notifications(
    journal_path: Path,
    *,
    match_id: str | None = None,
) -> list[dict[str, Any]]:
    """Atomically claim newly settled public rows that need a result card.

    The claim is persisted *before* Telegram delivery. This prevents the LIVE
    worker, menu reconciliation, or another process from reading the same
    ``result_notification_pending`` row and sending the same result repeatedly.
    A claim expires after a short TTL so a genuinely failed/crashed delivery can
    be retried later. A settlement correction gets a new notification version
    and is therefore allowed through even if an older claim still exists.
    """
    wanted = str(match_id or "")
    now = datetime.now(timezone.utc)
    claimed: list[dict[str, Any]] = []

    with _journal_delivery_lock(journal_path):
        rows = load_signal_journal(journal_path)
        dirty = False
        for row in rows:
            if not bool(row.get("result_notification_pending")):
                continue
            if str(row.get("result") or "").lower() not in FINAL_RESULTS:
                continue
            if wanted and str(row.get("match_id") or "") != wanted:
                continue
            if not was_publicly_sent(row):
                continue
            if _claim_is_active(row, now):
                continue

            row["result_notification_claim_id"] = uuid.uuid4().hex
            row["result_notification_claimed_at"] = now.isoformat()
            row["result_notification_claim_version"] = _notification_version(row)
            claimed.append(dict(row))
            dirty = True

        if dirty:
            save_signal_journal(journal_path, rows)

    return claimed


def finalize_result_delivery(
    journal_path: Path,
    row: dict[str, Any],
    sent: int,
) -> bool:
    """Mark one claimed result notification delivered.

    A stale worker is not allowed to finalize a newer corrected settlement: when
    the caller carries a claim id/version, both must still match the journal.
    """
    if int(sent or 0) <= 0:
        return False
    entry_key = str(row.get("entry_key") or "")
    if not entry_key:
        return False

    with _journal_delivery_lock(journal_path):
        rows = load_signal_journal(journal_path)
        index = next(
            (i for i, item in enumerate(rows) if str(item.get("entry_key") or "") == entry_key),
            None,
        )
        if index is None:
            return False

        current = rows[index]
        claim_id = str(row.get("result_notification_claim_id") or "")
        claim_version = str(row.get("result_notification_claim_version") or "")
        if claim_id and str(current.get("result_notification_claim_id") or "") != claim_id:
            return False
        if claim_version and _notification_version(current) != claim_version:
            return False

        current["result_notification_pending"] = False
        current["result_telegram_sent"] = True
        current["result_telegram_sent_at"] = _now()
        current["result_telegram_delivery_count"] = int(sent)
        _clear_claim(current)
        save_signal_journal(journal_path, rows)
    return True
