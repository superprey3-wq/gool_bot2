from __future__ import annotations

import os
import sys
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .journal import journal_lock, load_signal_journal, save_signal_journal


_LOCK = threading.RLock()
_INSTALLED = False
_ORIGINAL_SETTLE: Any = None
_FINAL = {"won", "lost", "push", "void"}


def _parse_dt(value: Any) -> datetime | None:
    raw = str(value or "").strip()
    if not raw:
        return None
    try:
        dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except Exception:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _age_hours(row: dict[str, Any]) -> float:
    dt = _parse_dt(row.get("telegram_sent_at") or row.get("created_at"))
    if dt is None:
        return 999.0
    return max(0.0, (datetime.now(timezone.utc) - dt.astimezone(timezone.utc)).total_seconds() / 3600.0)


def _identity(row: dict[str, Any], index: int) -> str:
    key = str(row.get("entry_key") or "").strip()
    if key:
        return f"entry:{key}"
    brain = str(row.get("brain_signal_key") or row.get("signal_key") or "").strip()
    if brain:
        return f"brain:{brain}"
    return f"row:{index}"


def _is_brain(row: dict[str, Any]) -> bool:
    return (
        str(row.get("entry_key") or "").startswith("brain:")
        or bool(row.get("tracking_only"))
        or str(row.get("accounting_mode") or "") == "result_only"
        or str(row.get("signal_source") or "").upper() == "GOOL_BRAIN"
    )


def _rank(row: dict[str, Any]) -> tuple[int, int, int, str]:
    result = str(row.get("result") or "pending").lower()
    final = int(result in _FINAL)
    result_sent = int(bool(row.get("result_telegram_sent")))
    canonical_brain = int(bool(row.get("tracking_only")))
    stamp = str(row.get("settled_at") or row.get("telegram_sent_at") or row.get("created_at") or "")
    return final, result_sent, canonical_brain, stamp


def _merge_duplicate_group(group: list[dict[str, Any]]) -> dict[str, Any]:
    canonical = dict(max(group, key=_rank))
    for row in group:
        for key, value in row.items():
            if key not in canonical or canonical.get(key) in {None, "", [], {}}:
                canonical[key] = value

    final_rows = [row for row in group if str(row.get("result") or "").lower() in _FINAL]
    if final_rows:
        best_final = max(final_rows, key=_rank)
        for key in (
            "result",
            "settled_at",
            "settled_minute",
            "settled_score",
            "settlement_source",
            "settled_stats_snapshot",
            "settled_cards",
        ):
            if key in best_final:
                canonical[key] = best_final.get(key)

    if any(bool(row.get("telegram_sent")) for row in group):
        canonical["telegram_sent"] = True
    if any(bool(row.get("result_telegram_sent")) for row in group):
        canonical["result_telegram_sent"] = True
        canonical["result_notification_pending"] = False
        sent_rows = [row for row in group if bool(row.get("result_telegram_sent"))]
        latest = max(sent_rows, key=lambda row: str(row.get("result_telegram_sent_at") or ""))
        canonical["result_telegram_sent_at"] = latest.get("result_telegram_sent_at")
        canonical["result_telegram_delivery_count"] = max(
            int(row.get("result_telegram_delivery_count") or 0) for row in sent_rows
        )

    if _is_brain(canonical) or any(_is_brain(row) for row in group):
        signal_key = str(
            canonical.get("brain_signal_key")
            or canonical.get("signal_key")
            or str(canonical.get("entry_key") or "").removeprefix("brain:")
        )
        canonical["entry_key"] = f"brain:{signal_key}" if signal_key else str(canonical.get("entry_key") or "")
        canonical["brain_signal_key"] = signal_key
        canonical["signal_key"] = signal_key
        canonical["tracking_only"] = True
        canonical["bank_tracking"] = False
        canonical["mode"] = "active"
        canonical["signal_source"] = "GOOL_BRAIN"
        canonical.pop("accounting_mode", None)
        canonical.pop("non_monetary", None)
        canonical.pop("virtual_bank_before_rub", None)
        canonical.pop("virtual_stake_rub", None)
        canonical.pop("virtual_stake_pct", None)
        canonical.pop("virtual_profit_rub", None)
        canonical["profit_units"] = None
        # A real delivered current row remains eligible. Historical migration
        # stays suppressed only if every duplicate explicitly says so.
        if any(row.get("result_card_eligible") is not False for row in group):
            canonical["result_card_eligible"] = True

    return canonical


def repair_public_journal(journal_path: Path) -> dict[str, int]:
    """Repair duplicate rows left by the old double Brain journal architecture."""
    path = Path(journal_path)
    if not path.exists():
        return {"before": 0, "after": 0, "removed": 0, "seeded": 0}

    with journal_lock(path):
        rows = load_signal_journal(path)
        groups: dict[str, list[dict[str, Any]]] = {}
        order: list[str] = []
        for index, row in enumerate(rows):
            key = _identity(row, index)
            if key not in groups:
                groups[key] = []
                order.append(key)
            groups[key].append(dict(row))
        repaired = [_merge_duplicate_group(groups[key]) for key in order]
        removed = len(rows) - len(repaired)
        changed = removed > 0 or repaired != rows
        if changed:
            save_signal_journal(path, repaired)

    seeded = 0
    try:
        from .result_delivery_once import seed_delivered_results

        seeded = seed_delivered_results(path, repaired)
    except Exception as exc:
        print(f"GOOL_JOURNAL_LEDGER_SEED_ERROR {type(exc).__name__}:{exc}", flush=True)
    print(
        f"GOOL_JOURNAL_REPAIR before={len(rows)} after={len(repaired)} removed={removed} seeded={seeded}",
        flush=True,
    )
    return {"before": len(rows), "after": len(repaired), "removed": removed, "seeded": seeded}


def _timeline_final_score(timeline: list[dict[str, Any]]) -> list[int] | None:
    final: list[int] | None = None
    for goal in sorted(timeline, key=lambda item: int(item.get("minute") or 0)):
        if str(goal.get("event_type") or "goal").strip().lower() not in {"", "goal"}:
            continue
        score = goal.get("score") or []
        try:
            if len(score) >= 2:
                final = [int(score[0] or 0), int(score[1] or 0)]
        except Exception:
            continue
    return final


def _stale_hours() -> float:
    try:
        return max(1.5, float(os.getenv("GOOL_PENDING_STALE_RECONCILE_HOURS", "3")))
    except (TypeError, ValueError):
        return 3.0


def _hard_expire_hours() -> float:
    try:
        return max(_stale_hours(), float(os.getenv("GOOL_PENDING_HARD_EXPIRE_HOURS", "4")))
    except (TypeError, ValueError):
        return 4.0


def reconcile_stale_pending(journal_path: Path) -> int:
    """Close journal rows that can no longer be reconciled by the normal LIVE feed.

    Normal Flashscore settlement runs first elsewhere. Here only old still-pending
    rows are handled. We try the event state/timeline once more; if an old event
    has disappeared and no authoritative score can be reconstructed, it becomes
    VOID rather than a guessed loss. Historical forced cleanup never emits a late
    result card.
    """
    path = Path(journal_path)
    with journal_lock(path):
        snapshot = [
            dict(row)
            for row in load_signal_journal(path)
            if str(row.get("result") or "pending").lower() == "pending"
            and str(row.get("mode") or "active").lower() == "active"
            and _age_hours(row) >= _stale_hours()
        ]
    if not snapshot:
        return 0

    ids = {str(row.get("match_id") or "") for row in snapshot if str(row.get("match_id") or "")}
    try:
        from .providers.flashscore import FlashscoreProvider

        provider = FlashscoreProvider()
        states = provider.event_states(ids) if ids else {}
    except Exception as exc:
        print(f"GOOL_STALE_RECONCILE_STATE_ERROR {type(exc).__name__}:{exc}", flush=True)
        states = {}
        try:
            from .providers.flashscore import FlashscoreProvider

            provider = FlashscoreProvider()
        except Exception:
            provider = None

    timeline_cache: dict[str, list[dict[str, Any]]] = {}
    records: dict[str, dict[str, Any]] = {}
    hard_expired: set[str] = set()
    from . import multi_menu

    for row in snapshot:
        mid = str(row.get("match_id") or "")
        if not mid:
            continue
        state = states.get(mid) if isinstance(states, dict) else None
        state = dict(state) if isinstance(state, dict) else {}
        finished = bool(state.get("is_finished")) or str(state.get("coarse_status") or "") == "3"
        age = _age_hours(row)
        if not finished and age < _hard_expire_hours():
            continue
        if mid not in timeline_cache:
            try:
                timeline_cache[mid] = provider.fetch_goal_timeline(mid) if provider is not None else []
            except Exception:
                timeline_cache[mid] = []
        timeline = timeline_cache[mid]
        final = _timeline_final_score(timeline)
        if not finished:
            if final is None:
                hard_expired.add(str(row.get("entry_key") or ""))
                continue
            state.update(
                {
                    "home_score": final[0],
                    "away_score": final[1],
                    "is_finished": True,
                    "coarse_status": "3",
                    "status_code": "3",
                }
            )
        records[mid] = multi_menu._synthetic_record(row, state, timeline)

    changed = 0
    if records:
        from . import multi_journal

        for mid, record in records.items():
            try:
                changed += len(multi_journal.settle_multi_journal(record, path))
            except Exception as exc:
                print(f"GOOL_STALE_RECONCILE_SETTLE_ERROR match={mid} {type(exc).__name__}:{exc}", flush=True)

    if hard_expired:
        now = datetime.now(timezone.utc).isoformat()
        with journal_lock(path):
            rows = load_signal_journal(path)
            dirty = False
            for row in rows:
                key = str(row.get("entry_key") or "")
                if key not in hard_expired or str(row.get("result") or "pending").lower() != "pending":
                    continue
                score = list(row.get("score") or [0, 0])
                row.update(
                    {
                        "result": "void",
                        "profit_units": None if bool(row.get("tracking_only")) else 0.0,
                        "settled_at": now,
                        "settled_minute": int(row.get("minute") or 0),
                        "settled_score": [int(score[0] or 0), int(score[1] or 0)],
                        "settlement_source": "stale_event_unavailable_safe_void",
                        "result_notification_pending": False,
                        "result_notification_suppressed": True,
                        "result_notification_suppressed_at": now,
                        "result_notification_suppression_reason": "stale_event_unavailable_safe_void",
                    }
                )
                dirty = True
                changed += 1
            if dirty:
                save_signal_journal(path, rows)

    if changed:
        print(f"GOOL_STALE_RECONCILE closed={changed} candidates={len(snapshot)}", flush=True)
    return changed


def _locked_settle(record: dict[str, Any], journal_path: Path) -> list[dict[str, Any]]:
    if _ORIGINAL_SETTLE is None:
        return []
    path = Path(journal_path)
    with journal_lock(path):
        return list(_ORIGINAL_SETTLE(record, path) or [])


def install_journal_integrity() -> None:
    """Install one lock boundary around every active Multi settlement binding."""
    global _INSTALLED, _ORIGINAL_SETTLE
    if _INSTALLED:
        return
    with _LOCK:
        if _INSTALLED:
            return
        from . import multi_journal, multi_menu, multi_runtime

        _ORIGINAL_SETTLE = multi_journal.settle_multi_journal
        multi_journal.settle_multi_journal = _locked_settle
        multi_menu.settle_multi_journal = _locked_settle
        multi_runtime.settle_multi_journal = _locked_settle
        reconcile_module = sys.modules.get("gool_bot2.multi_result_reconcile")
        if reconcile_module is not None and hasattr(reconcile_module, "settle_multi_journal"):
            setattr(reconcile_module, "settle_multi_journal", _locked_settle)
        _INSTALLED = True
        print("GOOL_JOURNAL_INTEGRITY installed transaction_lock=on", flush=True)


__all__ = ["install_journal_integrity", "reconcile_stale_pending", "repair_public_journal"]
