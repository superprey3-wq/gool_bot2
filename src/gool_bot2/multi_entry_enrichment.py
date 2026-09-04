from __future__ import annotations

from pathlib import Path
from typing import Any

from .journal import load_signal_journal, save_signal_journal
from .multi_public_metrics import brief_selection_reason, confidence_snapshot


def enrich_multi_entry(
    journal_path: Path,
    entry: dict[str, Any] | None,
    record: dict[str, Any],
    decision: Any,
    experts: dict[str, Any],
    *,
    data_quality: float,
) -> dict[str, Any] | None:
    if entry is None or getattr(decision, "winner", None) is None:
        return entry

    winner = decision.winner
    metrics = confidence_snapshot(
        record,
        winner,
        experts,
        data_quality=data_quality,
    )
    payload = {
        **metrics,
        "selection_reason": brief_selection_reason(decision, winner),
        "confidence_formula_version": 1,
        "live_momentum_snapshot": dict(record.get("live_momentum") or {}),
    }
    if str(getattr(winner, "source", "") or "").startswith("1xbet:autonomous_steam"):
        payload["signal_source"] = "STEAM_OVERRIDE"
    elif bool(getattr(winner, "market_override", False)) and not bool(getattr(winner, "expert_passed", True)):
        payload["signal_source"] = "MARKET_CONFIRM"
    else:
        payload["signal_source"] = "GOOL_STATE"

    entry.update(payload)

    entry_key = str(entry.get("entry_key") or "")
    if not entry_key:
        return entry

    rows = load_signal_journal(journal_path)
    changed = False
    for row in rows:
        if str(row.get("entry_key") or "") != entry_key:
            continue
        row.update(payload)
        changed = True
        break
    if changed:
        save_signal_journal(journal_path, rows)
    return entry
