from __future__ import annotations

from datetime import datetime, timezone
import os
from pathlib import Path
from typing import Any

from .journal import load_signal_journal, save_signal_journal


FINAL_RESULTS = {"won", "lost", "push", "void"}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


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


def _retry_due(row: dict[str, Any]) -> bool:
    raw = str(row.get("result_notification_last_attempt_at") or "").strip()
    if not raw:
        return True
    try:
        dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        elapsed = (datetime.now(timezone.utc) - dt.astimezone(timezone.utc)).total_seconds()
        return elapsed >= max(15.0, float(os.getenv("GOOL_RESULT_RETRY_SECONDS", "60")))
    except Exception:
        return True


def pending_result_notifications(
    journal_path: Path,
    *,
    match_id: str | None = None,
) -> list[dict[str, Any]]:
    """Return newly settled public rows whose result card still needs delivery.

    Only settlements created after the pending-result flag was introduced are
    eligible. This avoids replaying historical result cards after deployment.
    """
    wanted = str(match_id or "")
    out: list[dict[str, Any]] = []
    for row in load_signal_journal(journal_path):
        if not bool(row.get("result_notification_pending")):
            continue
        if str(row.get("result") or "").lower() not in FINAL_RESULTS:
            continue
        if wanted and str(row.get("match_id") or "") != wanted:
            continue
        if not was_publicly_sent(row):
            continue
        if not _retry_due(row):
            continue
        out.append(dict(row))
    return out


def finalize_result_delivery(
    journal_path: Path,
    row: dict[str, Any],
    sent: int,
) -> bool:
    """Mark one result notification delivered; failed sends stay pending for retry."""
    entry_key = str(row.get("entry_key") or "")
    if not entry_key:
        return False

    rows = load_signal_journal(journal_path)
    index = next(
        (i for i, item in enumerate(rows) if str(item.get("entry_key") or "") == entry_key),
        None,
    )
    if index is None:
        return False

    rows[index]["result_notification_last_attempt_at"] = _now()
    rows[index]["result_notification_attempts"] = int(rows[index].get("result_notification_attempts") or 0) + 1
    if int(sent or 0) > 0:
        rows[index]["result_notification_pending"] = False
        rows[index]["result_telegram_sent"] = True
        rows[index]["result_telegram_sent_at"] = _now()
        rows[index]["result_telegram_delivery_count"] = int(sent)
    save_signal_journal(journal_path, rows)
    return int(sent or 0) > 0
