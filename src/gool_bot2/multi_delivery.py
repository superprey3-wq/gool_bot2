from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .journal import load_signal_journal, save_signal_journal


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
