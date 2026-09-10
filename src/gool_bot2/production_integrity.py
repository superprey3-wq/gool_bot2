from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from .journal import load_signal_journal, save_signal_journal


_FINAL_RESULTS = {"won", "lost", "push", "void"}
_SETTLEMENT_FIELDS = (
    "result",
    "settled_at",
    "settled_minute",
    "settled_score",
    "settlement_source",
    "settled_stats_snapshot",
    "settled_cards",
    "settlement_corrected",
    "settlement_correction",
    "settlement_correction_reason",
)
_RESULT_DELIVERY_FIELDS = (
    "result_notification_pending",
    "result_notification_created_at",
    "result_notification_suppressed",
    "result_notification_suppressed_at",
    "result_notification_suppression_reason",
    "result_telegram_sent",
    "result_telegram_sent_at",
    "result_telegram_delivery_count",
)


def _stamp(row: dict[str, Any], *keys: str) -> str:
    for key in keys:
        value = str(row.get(key) or "")
        if value:
            return value
    return ""


def _is_brain(row: dict[str, Any]) -> bool:
    entry_key = str(row.get("entry_key") or "")
    source = str(row.get("signal_source") or row.get("source") or "")
    return bool(
        entry_key.startswith("brain:")
        or str(row.get("brain_signal_key") or "")
        or str(row.get("signal_key") or "")
        or "GOOL_BRAIN" in source.upper()
        or source.startswith("brain_primary:")
    )


def _identity(row: dict[str, Any], index: int) -> str:
    entry_key = str(row.get("entry_key") or "").strip()
    if entry_key:
        return entry_key
    brain_key = str(row.get("brain_signal_key") or row.get("signal_key") or "").strip()
    if brain_key:
        return f"brain:{brain_key}"
    # Rows without a stable key are intentionally never merged by this repair.
    return f"__row__:{index}"


def _normalize_brain(row: dict[str, Any]) -> None:
    signal_key = str(row.get("brain_signal_key") or row.get("signal_key") or "").strip()
    entry_key = str(row.get("entry_key") or "").strip()
    if not signal_key and entry_key.startswith("brain:"):
        signal_key = entry_key[len("brain:") :]
    if signal_key:
        row["signal_key"] = signal_key
        row["brain_signal_key"] = signal_key
        row["entry_key"] = f"brain:{signal_key}"

    if str(row.get("result") or "").lower() == "tracking":
        row["result"] = "pending"
    row["tracking_only"] = True
    row["bank_tracking"] = False
    row["non_monetary"] = True
    row["pipeline_version"] = "unified_v1"
    row.pop("accounting_mode", None)
    for key in (
        "virtual_bank_before_rub",
        "virtual_stake_rub",
        "virtual_stake_pct",
        "virtual_profit_rub",
    ):
        row.pop(key, None)
    if str(row.get("result") or "pending").lower() in _FINAL_RESULTS:
        row["profit_units"] = None


def repair_public_journal(path: Path) -> dict[str, int]:
    """Collapse duplicate rows left by the two historical Brain journal systems.

    During the broken period both ``brain_journal_tracking`` and
    ``brain_journal_results`` could persist the same public Brain signal. One row
    used ``pending`` and the other ``tracking``; later they could settle/send
    independently. This startup repair is idempotent and keeps one canonical row
    per entry_key while preserving the strongest settlement/delivery evidence.
    """
    path = Path(path)
    rows = load_signal_journal(path)
    if not rows:
        return {"before": 0, "after": 0, "duplicates_removed": 0, "brain_normalized": 0}

    groups: dict[str, list[tuple[int, dict[str, Any]]]] = {}
    for index, row in enumerate(rows):
        groups.setdefault(_identity(row, index), []).append((index, dict(row)))

    repaired: list[tuple[int, dict[str, Any]]] = []
    duplicates_removed = 0
    brain_normalized = 0

    for key, items in groups.items():
        if len(items) == 1:
            index, row = items[0]
            if _is_brain(row):
                before = dict(row)
                _normalize_brain(row)
                brain_normalized += int(row != before)
            repaired.append((index, row))
            continue

        duplicates_removed += len(items) - 1
        # Prefer the modern tracking row as the structural base, otherwise the
        # newest row. Settlement state is merged separately below.
        structural = sorted(
            items,
            key=lambda pair: (
                bool(pair[1].get("tracking_only")),
                _stamp(pair[1], "created_at", "telegram_sent_at"),
            ),
            reverse=True,
        )[0]
        keep_index, keep = structural[0], dict(structural[1])

        finals = [
            row for _, row in items
            if str(row.get("result") or "").lower() in _FINAL_RESULTS
        ]
        if finals:
            final = max(finals, key=lambda row: _stamp(row, "settled_at", "created_at"))
            for field in _SETTLEMENT_FIELDS:
                if field in final:
                    keep[field] = final[field]
        elif any(str(row.get("result") or "").lower() in {"pending", "tracking"} for _, row in items):
            keep["result"] = "pending"

        # Successful signal/result delivery in ANY duplicate is authoritative.
        if any(bool(row.get("telegram_sent")) for _, row in items):
            keep["telegram_sent"] = True
            sent_rows = [row for _, row in items if bool(row.get("telegram_sent"))]
            keep["telegram_delivery_count"] = max(
                int(row.get("telegram_delivery_count") or 1) for row in sent_rows
            )
            sent_at = [str(row.get("telegram_sent_at") or "") for row in sent_rows if row.get("telegram_sent_at")]
            if sent_at:
                keep["telegram_sent_at"] = min(sent_at)

        for field in _RESULT_DELIVERY_FIELDS:
            candidates = [row.get(field) for _, row in items if field in row]
            if candidates and field not in keep:
                keep[field] = candidates[-1]

        if any(bool(row.get("result_telegram_sent")) for _, row in items):
            keep["result_telegram_sent"] = True
            keep["result_notification_pending"] = False
            sent_at = [
                str(row.get("result_telegram_sent_at") or "")
                for _, row in items
                if row.get("result_telegram_sent_at")
            ]
            if sent_at:
                keep["result_telegram_sent_at"] = min(sent_at)
            keep["result_telegram_delivery_count"] = max(
                int(row.get("result_telegram_delivery_count") or 1)
                for _, row in items
                if bool(row.get("result_telegram_sent"))
            )
        elif finals:
            keep["result_notification_pending"] = any(
                bool(row.get("result_notification_pending")) for _, row in items
            )

        if _is_brain(keep) or key.startswith("brain:"):
            _normalize_brain(keep)
            brain_normalized += 1

        repaired.append((min(index for index, _ in items), keep))

    repaired.sort(key=lambda pair: pair[0])
    out = [row for _, row in repaired]
    if out != rows:
        save_signal_journal(path, out)

    stats = {
        "before": len(rows),
        "after": len(out),
        "duplicates_removed": duplicates_removed,
        "brain_normalized": brain_normalized,
    }
    print(
        "GOOL_JOURNAL_REPAIR "
        + " ".join(f"{key}={value}" for key, value in stats.items()),
        flush=True,
    )
    return stats


def audit_production_bindings(*, strict: bool | None = None) -> dict[str, bool]:
    """Verify the public worker has exactly one Brain/result/menu pipeline."""
    from . import brain_card_restore as card
    from . import brain_journal_tracking as tracking
    from . import brain_primary_mode as brain
    from . import journal_in_game
    from . import multi_runtime
    from . import multi_telegram
    from . import result_delivery_guard as delivery_guard
    from . import telegram

    checks = {
        "brain_runtime": multi_runtime.sync_multi_journal is brain.sync_brain_or_market_journal,
        "brain_card": multi_runtime.emit_multi_signal is card.emit_brain_card_signal,
        "brain_tracking": brain._mark_brain_sent is tracking._mark_brain_sent_with_journal,
        "result_live_guard": multi_runtime.emit_multi_results is delivery_guard._guarded_emit,
        "result_menu_guard": multi_telegram.emit_multi_results is delivery_guard._guarded_emit,
        "in_game_journal": telegram.in_game_sections is journal_in_game.journal_in_game_sections,
    }
    ok = all(checks.values())
    print(
        f"GOOL_PIPELINE_AUDIT ok={int(ok)} "
        + " ".join(f"{key}={int(value)}" for key, value in checks.items()),
        flush=True,
    )

    if strict is None:
        strict = str(os.getenv("GOOL_PIPELINE_STRICT_AUDIT", "1")).strip().lower() not in {
            "0", "false", "no", "off"
        }
    if strict and not ok:
        failed = ",".join(key for key, value in checks.items() if not value)
        raise RuntimeError(f"gool_pipeline_audit_failed={failed}")
    return checks


__all__ = ["audit_production_bindings", "repair_public_journal"]
