from __future__ import annotations

import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .journal import load_signal_journal, save_signal_journal


FINAL_RESULTS = {"won", "lost", "push", "void"}


def _parse_dt(value: Any) -> datetime | None:
    raw = str(value or "").strip()
    if not raw:
        return None
    try:
        dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except Exception:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _max_replay_age_hours() -> float:
    try:
        return max(0.25, float(os.getenv("GOOL_RESULT_REPLAY_MAX_SIGNAL_AGE_HOURS", "4")))
    except (TypeError, ValueError):
        return 4.0


def _identity(row: dict[str, Any]) -> str:
    key = str(row.get("entry_key") or "").strip()
    if key:
        return key
    signal_key = str(row.get("brain_signal_key") or row.get("signal_key") or "").strip()
    if signal_key and (
        str(row.get("source") or "").startswith("brain_primary:")
        or str(row.get("signal_source") or "") == "GOOL_BRAIN"
    ):
        return f"brain:{signal_key}"
    return ""


def _is_brain(row: dict[str, Any]) -> bool:
    return bool(
        str(row.get("entry_key") or "").startswith("brain:")
        or str(row.get("source") or "").startswith("brain_primary:")
        or str(row.get("signal_source") or "") == "GOOL_BRAIN"
        or row.get("tracking_only") is True
        or str(row.get("accounting_mode") or "") == "result_only"
    )


def _rank(row: dict[str, Any]) -> tuple[int, int, str, str]:
    result = str(row.get("result") or "pending").lower()
    result_rank = 4 if result in FINAL_RESULTS else 3 if result == "pending" else 2 if result == "tracking" else 1
    sent_rank = 1 if bool(row.get("result_telegram_sent")) else 0
    settled = str(row.get("settled_at") or "")
    created = str(row.get("created_at") or row.get("telegram_sent_at") or "")
    return result_rank, sent_rank, settled, created


def _copy_missing(target: dict[str, Any], source: dict[str, Any]) -> None:
    for key, value in source.items():
        current = target.get(key)
        if current is None or current == "" or current == [] or current == {}:
            if value is not None and value != "" and value != [] and value != {}:
                target[key] = value


def _normalize_brain(row: dict[str, Any], identity: str) -> None:
    result = str(row.get("result") or "pending").lower()
    if result in {"tracking", "signal_only", ""}:
        row["result"] = "pending"
    row["entry_key"] = identity
    signal_key = str(row.get("brain_signal_key") or row.get("signal_key") or "").strip()
    if signal_key:
        row["signal_key"] = signal_key
    row["mode"] = "active"
    row["signal_source"] = "GOOL_BRAIN"
    row["tracking_only"] = True
    row["bank_tracking"] = False
    row.pop("accounting_mode", None)
    row.pop("non_monetary", None)
    row["profit_units"] = None
    try:
        if float(row.get("odd") or 0.0) <= 1.0:
            row["odd"] = None
            row["price_available"] = False
    except (TypeError, ValueError):
        row["odd"] = None
        row["price_available"] = False
    for key in (
        "virtual_bank_before_rub",
        "virtual_stake_rub",
        "virtual_stake_pct",
        "virtual_profit_rub",
    ):
        row.pop(key, None)


def _suppress_old_unsent_result(row: dict[str, Any]) -> None:
    result = str(row.get("result") or "").lower()
    if result not in FINAL_RESULTS or bool(row.get("result_telegram_sent")):
        return
    created = _parse_dt(row.get("created_at") or row.get("telegram_sent_at"))
    if created is None:
        return
    age = (datetime.now(timezone.utc) - created.astimezone(timezone.utc)).total_seconds() / 3600.0
    if age <= _max_replay_age_hours():
        return
    row["result_notification_pending"] = False
    row["result_notification_suppressed"] = True
    row["result_notification_suppression_reason"] = "startup_repair_historical_result"
    row["result_notification_suppressed_at"] = datetime.now(timezone.utc).isoformat()
    for key in (
        "result_notification_claim_id",
        "result_notification_claimed_at",
        "result_notification_claim_version",
    ):
        row.pop(key, None)


def _merge_group(identity: str, group: list[dict[str, Any]]) -> dict[str, Any]:
    base = dict(max(group, key=_rank))
    for row in sorted(group, key=_rank, reverse=True):
        _copy_missing(base, row)

    if any(bool(row.get("telegram_sent")) for row in group):
        base["telegram_sent"] = True
        base["telegram_delivery_count"] = max(
            [int(row.get("telegram_delivery_count") or 0) for row in group] or [1]
        )

    delivered = [row for row in group if bool(row.get("result_telegram_sent"))]
    if delivered:
        latest = max(delivered, key=lambda row: str(row.get("result_telegram_sent_at") or ""))
        base["result_notification_pending"] = False
        base["result_telegram_sent"] = True
        base["result_telegram_sent_at"] = latest.get("result_telegram_sent_at")
        base["result_telegram_delivery_count"] = max(
            [int(row.get("result_telegram_delivery_count") or 0) for row in delivered] or [1]
        )
        for key in (
            "result_notification_claim_id",
            "result_notification_claimed_at",
            "result_notification_claim_version",
        ):
            base.pop(key, None)

    if _is_brain(base) or any(_is_brain(row) for row in group):
        _normalize_brain(base, identity)

    _suppress_old_unsent_result(base)
    return base


def repair_public_journal(journal_path: Path) -> dict[str, int]:
    """Normalize the public journal after the old dual-Brain pipeline.

    Older deployments could write the same Brain signal twice: one
    ``tracking_only/pending`` row and one ``result_only/tracking`` row. This
    startup repair collapses exact signal identities, preserves the most advanced
    settlement, keeps already-delivered results delivered, and converts remaining
    Brain rows to the one canonical tracking schema used by production now.
    """
    path = Path(journal_path)
    rows = load_signal_journal(path)
    if not rows:
        return {"before": 0, "after": 0, "duplicates_removed": 0, "normalized": 0}

    groups: dict[str, list[dict[str, Any]]] = {}
    order: list[str] = []
    anonymous = 0
    for raw in rows:
        row = dict(raw)
        identity = _identity(row)
        if not identity:
            anonymous += 1
            identity = f"__anonymous__:{anonymous}"
        if identity not in groups:
            groups[identity] = []
            order.append(identity)
        groups[identity].append(row)

    repaired: list[dict[str, Any]] = []
    normalized = 0
    for identity in order:
        group = groups[identity]
        if identity.startswith("__anonymous__:"):
            repaired.append(group[-1])
            continue
        merged = _merge_group(identity, group)
        if len(group) > 1 or merged != group[-1]:
            normalized += 1
        repaired.append(merged)

    changed = repaired != rows
    if changed:
        save_signal_journal(path, repaired)

    result = {
        "before": len(rows),
        "after": len(repaired),
        "duplicates_removed": max(0, len(rows) - len(repaired)),
        "normalized": normalized,
    }
    if changed:
        print(
            "GOOL_JOURNAL_REPAIR "
            f"before={result['before']} after={result['after']} "
            f"duplicates_removed={result['duplicates_removed']} normalized={result['normalized']}",
            flush=True,
        )
    return result


__all__ = ["repair_public_journal"]
