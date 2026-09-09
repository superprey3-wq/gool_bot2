from __future__ import annotations

import os
import sys
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from .journal import load_signal_journal, save_signal_journal


_LOCK = threading.Lock()
_INSTALLED = False
_ORIGINAL_MARK_BRAIN_SENT: Any = None
_ORIGINAL_FINISH_ROW: Any = None
_ORIGINAL_BANK_ELIGIBLE: Any = None
_ORIGINAL_RECORD_MULTI_ENTRY: Any = None
_ORIGINAL_MENU_ROI: Any = None
_ORIGINAL_APPEND_BANK_STRIP: Any = None
_ORIGINAL_RESULT_CAPTION: Any = None
_ORIGINAL_WAS_PUBLICLY_SENT: Any = None
_ORIGINAL_RECONCILE_PENDING: Any = None


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _journal_path() -> Path:
    raw = os.getenv("GOOL_MULTI_JOURNAL_PATH", "").strip()
    if raw:
        return Path(raw)
    runtime = Path(os.getenv("RUNTIME_DATA_DIR", "data"))
    return runtime / "live" / "gool_multi_journal.json"


def _parse_dt(value: Any) -> datetime | None:
    raw = str(value or "").strip()
    if not raw:
        return None
    try:
        dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except Exception:
        return None


def _number(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _tracking_row(entry: dict[str, Any], sent: int, *, historical: bool = False) -> dict[str, Any] | None:
    signal_key = str(entry.get("signal_key") or "")
    match_id = str(entry.get("match_id") or "")
    strategy = str(entry.get("strategy") or entry.get("head") or "")
    if not signal_key or not match_id or strategy not in {"goal_before_ht", "another_goal"}:
        return None

    score = list(entry.get("score") or [0, 0])
    try:
        hs, aws = int(score[0] or 0), int(score[1] or 0)
    except Exception:
        hs, aws = 0, 0
    line = float(hs + aws) + 0.5
    family = "first_half_total" if strategy == "goal_before_ht" else "match_total"
    market_key = f"{family}:{line:g}"
    market = str(entry.get("market") or (f"1Т ТБ {line:g}" if strategy == "goal_before_ht" else f"ТБ {line:g}"))

    raw_odd = _number(entry.get("odd"))
    odd = raw_odd if raw_odd is not None and raw_odd > 1.0 else None
    created_at = str(entry.get("created_at") or entry.get("telegram_sent_at") or _now())
    sent_at = str(entry.get("telegram_sent_at") or _now())

    row = dict(entry)
    row.update(
        {
            "created_at": created_at,
            "mode": "active",
            "head": "multi",
            "entry_key": f"brain:{signal_key}",
            "market_key": market_key,
            "market_family": family,
            "market": market,
            "strategy": strategy,
            "odd": odd,
            "price_available": bool(odd is not None),
            "signal_source": "GOOL_BRAIN",
            "source": str(entry.get("source") or f"brain_primary:{strategy}"),
            "tracking_only": True,
            "bank_tracking": False,
            "result": "pending",
            "profit_units": None,
            "telegram_sent": True,
            "telegram_sent_at": sent_at,
            "telegram_delivery_count": max(1, int(sent or entry.get("telegram_delivery_count") or 1)),
            # Existing signal_only rows are migrated for journal counts, but do
            # not replay a burst of historical result cards after deployment.
            "result_card_eligible": not historical,
        }
    )
    row.pop("virtual_bank_before_rub", None)
    row.pop("virtual_stake_rub", None)
    row.pop("virtual_stake_pct", None)
    row.pop("virtual_profit_rub", None)
    return row


def _persist_tracking_row(row: dict[str, Any]) -> bool:
    path = _journal_path()
    rows = load_signal_journal(path)
    entry_key = str(row.get("entry_key") or "")
    if not entry_key:
        return False
    if any(str(item.get("entry_key") or "") == entry_key for item in rows):
        return False
    rows.append(dict(row))
    save_signal_journal(path, rows)
    return True


def _mark_brain_sent_with_journal(entry: dict[str, Any], sent: int) -> None:
    if int(sent or 0) <= 0:
        if _ORIGINAL_MARK_BRAIN_SENT is not None:
            _ORIGINAL_MARK_BRAIN_SENT(entry, sent)
        return

    row = _tracking_row(entry, sent, historical=False)
    if row is not None:
        # Mutate the live entry before the original marker and before
        # finalize_multi_delivery() runs in multi_runtime. That gives the normal
        # delivery finalizer a real active journal key without inventing a bet.
        entry.update(row)

    if _ORIGINAL_MARK_BRAIN_SENT is not None:
        _ORIGINAL_MARK_BRAIN_SENT(entry, sent)

    if row is None:
        return
    try:
        created = _persist_tracking_row(row)
        print(
            f"GOOL_BRAIN_JOURNAL match={row.get('match_id')} strategy={row.get('strategy')} "
            f"created={int(created)} tracking_only=1",
            flush=True,
        )
    except Exception as exc:
        print(f"GOOL_BRAIN_JOURNAL_ERROR {type(exc).__name__}:{exc}", flush=True)


def _finish_row_without_fake_profit(
    row: dict[str, Any],
    *,
    result: str,
    minute: int,
    score: list[int],
    reason: str,
) -> None:
    if _ORIGINAL_FINISH_ROW is None:
        return
    _ORIGINAL_FINISH_ROW(row, result=result, minute=minute, score=score, reason=reason)
    if not bool(row.get("tracking_only")):
        return
    row["profit_units"] = None
    row.pop("virtual_bank_before_rub", None)
    row.pop("virtual_stake_rub", None)
    row.pop("virtual_stake_pct", None)
    row.pop("virtual_profit_rub", None)


def _bank_eligible_without_tracking(row: dict[str, Any], state: dict[str, Any]) -> bool:
    if bool(row.get("tracking_only")):
        return False
    return bool(_ORIGINAL_BANK_ELIGIBLE(row, state)) if _ORIGINAL_BANK_ELIGIBLE is not None else False


def _record_multi_entry_independent(
    record: dict[str, Any],
    decision: Any,
    experts: dict[str, Any],
    journal_path: Path,
    *,
    data_quality: float,
) -> dict[str, Any] | None:
    """Keep autonomous STEAM independent from tracking-only Brain rows."""
    from . import multi_journal as journal

    candidate = journal.entry_from_decision(record, decision, experts, data_quality=data_quality)
    if candidate is None:
        return None
    rows = load_signal_journal(journal_path)
    match_id = str(candidate.get("match_id") or "")
    if any(
        str(row.get("match_id") or "") == match_id
        and str(row.get("result") or "pending").lower() == "pending"
        and not bool(row.get("tracking_only"))
        for row in rows
    ):
        return None
    if any(str(row.get("entry_key") or "") == str(candidate.get("entry_key") or "") for row in rows):
        return None
    journal.attach_entry_fields(candidate, rows, journal_path)
    rows.append(candidate)
    save_signal_journal(journal_path, rows)
    return candidate


def _roi_without_tracking(rows: list[dict[str, Any]]) -> str:
    priced = [row for row in rows if not bool(row.get("tracking_only"))]
    return _ORIGINAL_MENU_ROI(priced) if _ORIGINAL_MENU_ROI is not None else "—"


def _append_bank_strip_without_tracking(png: bytes, entry: dict[str, Any] | None, *, result: bool = False) -> bytes:
    if isinstance(entry, dict) and bool(entry.get("tracking_only")):
        return png
    if _ORIGINAL_APPEND_BANK_STRIP is None:
        return png
    return _ORIGINAL_APPEND_BANK_STRIP(png, entry, result=result)


def _result_caption_without_fake_odd(row: dict[str, Any]) -> str:
    if not bool(row.get("tracking_only")) or _ORIGINAL_RESULT_CAPTION is None:
        return _ORIGINAL_RESULT_CAPTION(row) if _ORIGINAL_RESULT_CAPTION is not None else ""
    result = str(row.get("result") or "void").lower()
    if result == "won":
        icon, label = "✅", "ЗАШЁЛ"
    elif result == "lost":
        icon, label = "❌", "НЕ ЗАШЁЛ"
    else:
        icon, label = "↩️", "ВОЗВРАТ / VOID"
    score = list(row.get("settled_score") or [0, 0])
    odd = _number(row.get("odd"))
    odd_text = f" @ {odd:.2f}" if odd is not None and odd > 1.0 else ""
    return (
        f"{icon} <b>{label} · GOOL BRAIN</b>\n"
        f"{row.get('home','?')} — {row.get('away','?')}\n"
        f"{row.get('market','?')}{odd_text}\n"
        f"{int(row.get('settled_minute') or 0)}' · {int(score[0] or 0)}:{int(score[1] or 0)}"
    )


def _was_publicly_sent_with_migration_guard(row: dict[str, Any]) -> bool:
    if row.get("result_card_eligible") is False:
        return False
    return bool(_ORIGINAL_WAS_PUBLICLY_SENT(row)) if _ORIGINAL_WAS_PUBLICLY_SENT is not None else False


def _reconcile_and_deliver_results() -> int:
    changed = int(_ORIGINAL_RECONCILE_PENDING() or 0) if _ORIGINAL_RECONCILE_PENDING is not None else 0
    try:
        from .multi_delivery import pending_result_notifications
        from .multi_telegram import emit_multi_results

        path = _journal_path()
        pending = pending_result_notifications(path)
        if pending:
            emit_multi_results({}, pending, journal_path=path)
    except Exception as exc:
        print(f"GOOL_BRAIN_RESULT_RETRY_ERROR {type(exc).__name__}:{exc}", flush=True)
    return changed


def _backfill_recent_signal_only() -> int:
    """Move recent already-sent Brain signal_only rows into the public journal.

    This fixes today's/yesterday's counts after deployment. Historical migrated
    rows settle normally but are marked not to replay old Telegram result cards.
    """
    try:
        from . import brain_primary_mode as brain

        state = brain._load_signal_state(brain._signal_state_path())
        signals = state.get("signals") or {}
    except Exception:
        return 0
    try:
        hours = max(0.0, float(os.getenv("GOOL_BRAIN_JOURNAL_BACKFILL_HOURS", "36")))
    except (TypeError, ValueError):
        hours = 36.0
    cutoff = datetime.now(timezone.utc) - timedelta(hours=hours)
    created = 0
    for raw in signals.values() if isinstance(signals, dict) else []:
        if not isinstance(raw, dict) or not bool(raw.get("telegram_sent")):
            continue
        dt = _parse_dt(raw.get("telegram_sent_at") or raw.get("created_at"))
        if dt is None or dt.astimezone(timezone.utc) < cutoff:
            continue
        row = _tracking_row(raw, int(raw.get("telegram_delivery_count") or 1), historical=True)
        if row is None:
            continue
        try:
            created += int(_persist_tracking_row(row))
        except Exception:
            continue
    return created


def install_brain_journal_tracking() -> None:
    global _INSTALLED
    global _ORIGINAL_MARK_BRAIN_SENT, _ORIGINAL_FINISH_ROW, _ORIGINAL_BANK_ELIGIBLE
    global _ORIGINAL_RECORD_MULTI_ENTRY, _ORIGINAL_MENU_ROI, _ORIGINAL_APPEND_BANK_STRIP
    global _ORIGINAL_RESULT_CAPTION, _ORIGINAL_WAS_PUBLICLY_SENT, _ORIGINAL_RECONCILE_PENDING

    if _INSTALLED:
        return
    with _LOCK:
        if _INSTALLED:
            return
        from . import brain_primary_mode as brain
        from . import multi_bank as bank
        from . import multi_delivery as delivery
        from . import multi_journal as journal
        from . import multi_menu as menu
        from . import multi_telegram as telegram

        _ORIGINAL_MARK_BRAIN_SENT = brain._mark_brain_sent
        _ORIGINAL_FINISH_ROW = journal._finish_row
        _ORIGINAL_BANK_ELIGIBLE = bank._eligible
        _ORIGINAL_RECORD_MULTI_ENTRY = journal.record_multi_entry
        _ORIGINAL_MENU_ROI = menu._roi
        _ORIGINAL_APPEND_BANK_STRIP = telegram.append_bank_strip
        _ORIGINAL_RESULT_CAPTION = telegram._result_caption
        _ORIGINAL_WAS_PUBLICLY_SENT = delivery.was_publicly_sent
        _ORIGINAL_RECONCILE_PENDING = menu.reconcile_pending

        brain._mark_brain_sent = _mark_brain_sent_with_journal
        journal._finish_row = _finish_row_without_fake_profit
        journal.record_multi_entry = _record_multi_entry_independent
        bank._eligible = _bank_eligible_without_tracking
        menu._roi = _roi_without_tracking
        menu.reconcile_pending = _reconcile_and_deliver_results
        telegram.append_bank_strip = _append_bank_strip_without_tracking
        telegram._result_caption = _result_caption_without_fake_odd
        delivery.was_publicly_sent = _was_publicly_sent_with_migration_guard
        telegram.was_publicly_sent = _was_publicly_sent_with_migration_guard

        # multi_product imported reconcile_pending by value before this installer
        # runs. Replace that module binding too so the Journal/In Game force
        # reconcile path retries result cards if the final LIVE tick was missed.
        product = sys.modules.get("gool_bot2.multi_product")
        if product is not None:
            setattr(product, "reconcile_pending", _reconcile_and_deliver_results)

        _INSTALLED = True
        migrated = _backfill_recent_signal_only()
        print(
            f"GOOL_BRAIN_JOURNAL_TRACKING installed result_cards=on migrated={migrated} "
            "bank_tracking=off steam_independent=1",
            flush=True,
        )


__all__ = ["install_brain_journal_tracking"]
