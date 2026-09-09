from __future__ import annotations

import json
import os
import threading
from pathlib import Path
from typing import Any


_LOCK = threading.RLock()
_INSTALLED = False
_ORIGINAL_MARK: Any = None
_ORIGINAL_SETTLE_ENTRY: Any = None
_ORIGINAL_FINISH_ROW: Any = None
_ORIGINAL_RECONCILE_FIRST_HALF: Any = None
_ORIGINAL_EMIT_RESULTS: Any = None
_ORIGINAL_MENU_RECONCILE: Any = None
_ORIGINAL_STATS_LINE: Any = None
_ORIGINAL_ROI: Any = None
_ORIGINAL_PROFIT: Any = None

ACCOUNTING_MODE = "result_only"
TRACKING_RESULT = "tracking"


def _runtime() -> Path:
    return Path(os.getenv("RUNTIME_DATA_DIR", "data"))


def _journal_path() -> Path:
    raw = os.getenv("GOOL_MULTI_JOURNAL_PATH", "").strip()
    return Path(raw) if raw else _runtime() / "live" / "gool_multi_journal.json"


def _brain_state_path() -> Path:
    raw = os.getenv("GOOL_BRAIN_SIGNAL_STATE_PATH", "").strip()
    return Path(raw) if raw else _runtime() / "live" / "gool_brain_signal_state.json"


def _score(value: Any) -> list[int]:
    try:
        if isinstance(value, (list, tuple)) and len(value) >= 2:
            return [int(value[0] or 0), int(value[1] or 0)]
    except Exception:
        pass
    return [0, 0]


def _market_fields(entry: dict[str, Any]) -> tuple[str, str]:
    strategy = str(entry.get("strategy") or entry.get("head") or "")
    score = _score(entry.get("score"))
    line = float(score[0] + score[1]) + 0.5
    if strategy == "goal_before_ht":
        return "first_half_total", f"first_half_total:{line:g}"
    return "match_total", f"match_total:{line:g}"


def _journal_row(entry: dict[str, Any], sent: int) -> dict[str, Any]:
    family, market_key = _market_fields(entry)
    signal_key = str(entry.get("signal_key") or "")
    odd = 0.0
    try:
        odd = float(entry.get("odd") or 0.0)
    except (TypeError, ValueError):
        odd = 0.0
    row = dict(entry)
    row.update(
        {
            "entry_key": f"brain:{signal_key}",
            "brain_signal_key": signal_key,
            "mode": "active",
            "accounting_mode": ACCOUNTING_MODE,
            "non_monetary": True,
            "market_family": family,
            "market_key": market_key,
            "odd": odd,
            "price_available": bool(odd > 1.0),
            "signal_source": "GOOL_BRAIN",
            "result": TRACKING_RESULT,
            "profit_units": None,
            "virtual_bank_before_rub": 0.0,
            "virtual_stake_rub": 0.0,
            "virtual_stake_pct": 0.0,
            "virtual_profit_rub": None,
            "telegram_sent": True,
            "telegram_delivery_count": int(sent or entry.get("telegram_delivery_count") or 1),
            "telegram_sent_at": entry.get("telegram_sent_at") or entry.get("created_at"),
        }
    )
    return row


def _persist_entry(entry: dict[str, Any], sent: int) -> bool:
    if int(sent or 0) <= 0 or not entry.get("signal_key"):
        return False
    from .journal import load_signal_journal, save_signal_journal

    path = _journal_path()
    with _LOCK:
        rows = load_signal_journal(path)
        key = str(entry.get("signal_key") or "")
        if any(str(row.get("brain_signal_key") or "") == key for row in rows):
            return False
        rows.append(_journal_row(entry, sent))
        save_signal_journal(path, rows)
    print(f"GOOL_BRAIN_JOURNAL tracked signal={key} path={path}", flush=True)
    return True


def _mark_and_journal(entry: dict[str, Any], sent: int) -> None:
    if _ORIGINAL_MARK is not None:
        _ORIGINAL_MARK(entry, sent)
    _persist_entry(entry, sent)


def _is_result_only(row: dict[str, Any]) -> bool:
    return str(row.get("accounting_mode") or "") == ACCOUNTING_MODE


def _settle_tracking(row: dict[str, Any], record: dict[str, Any]) -> bool:
    if _ORIGINAL_SETTLE_ENTRY is None:
        return False
    if not (_is_result_only(row) and str(row.get("result") or "") == TRACKING_RESULT):
        return bool(_ORIGINAL_SETTLE_ENTRY(row, record))

    row["result"] = "pending"
    changed = bool(_ORIGINAL_SETTLE_ENTRY(row, record))
    if not changed and str(row.get("result") or "") == "pending":
        row["result"] = TRACKING_RESULT
    return changed


def _finish_result_only(row: dict[str, Any], **kwargs: Any) -> None:
    assert _ORIGINAL_FINISH_ROW is not None
    _ORIGINAL_FINISH_ROW(row, **kwargs)
    if _is_result_only(row):
        row["profit_units"] = 0.0
        row["virtual_profit_rub"] = 0.0


def _reconcile_first_half(row: dict[str, Any], record: dict[str, Any]) -> bool:
    if _is_result_only(row) and str(row.get("result") or "") == TRACKING_RESULT:
        return False
    if _ORIGINAL_RECONCILE_FIRST_HALF is None:
        return False
    return bool(_ORIGINAL_RECONCILE_FIRST_HALF(row, record))


def _result_fallback(row: dict[str, Any]) -> str:
    result = str(row.get("result") or "void").lower()
    icon, label = ("✅", "ЗАШЁЛ") if result == "won" else (("❌", "НЕ ЗАШЁЛ") if result == "lost" else ("↩️", "VOID"))
    score = _score(row.get("settled_score"))
    odd = 0.0
    try:
        odd = float(row.get("odd") or 0.0)
    except (TypeError, ValueError):
        odd = 0.0
    price = f" @ {odd:.2f}" if odd > 1.0 else ""
    return (
        f"{icon} <b>GOOL BRAIN · {label}</b>\n"
        f"{row.get('home','?')} — {row.get('away','?')}\n"
        f"🎯 {row.get('market','?')}{price}\n"
        f"Результат: {int(row.get('settled_minute') or 0)}' · {score[0]}:{score[1]}"
    )


def _emit_results(record: dict[str, Any], rows: list[dict[str, Any]], *, journal_path: Path | None = None) -> int:
    from . import telegram
    from .multi_card import render_multi_result_card
    from .multi_delivery import finalize_result_delivery, was_publicly_sent

    brain_rows = [row for row in rows if _is_result_only(row)]
    normal_rows = [row for row in rows if not _is_result_only(row)]
    total = 0
    if normal_rows and _ORIGINAL_EMIT_RESULTS is not None:
        total += int(_ORIGINAL_EMIT_RESULTS(record, normal_rows, journal_path=journal_path) or 0)

    for row in brain_rows:
        if not was_publicly_sent(row):
            continue
        render_row = dict(row)
        try:
            if float(render_row.get("odd") or 0.0) <= 1.0:
                render_row["odd"] = None
        except (TypeError, ValueError):
            render_row["odd"] = None
        sent = 0
        try:
            sent = telegram.broadcast_photo(render_multi_result_card(render_row, record))
        except Exception as exc:
            print(f"GOOL_BRAIN_RESULT_CARD_ERROR {type(exc).__name__}:{exc}", flush=True)
        if sent == 0:
            sent = telegram.broadcast(_result_fallback(row))
        total += int(sent or 0)
        finalized = False
        if sent > 0 and journal_path is not None:
            finalized = finalize_result_delivery(journal_path, row, sent)
        print(
            f"GOOL_BRAIN_RESULT_SENT match={row.get('match_id')} result={row.get('result')} "
            f"sent={sent} finalized={int(finalized)}",
            flush=True,
        )
    return total


def _stats_line(rows: list[dict[str, Any]]) -> str:
    assert _ORIGINAL_STATS_LINE is not None
    normalized: list[dict[str, Any]] = []
    for row in rows:
        if _is_result_only(row) and str(row.get("result") or "") == TRACKING_RESULT:
            row = {**row, "result": "pending"}
        normalized.append(row)
    return _ORIGINAL_STATS_LINE(normalized)


def _roi(rows: list[dict[str, Any]]) -> str:
    assert _ORIGINAL_ROI is not None
    return _ORIGINAL_ROI([row for row in rows if not _is_result_only(row)])


def _profit(rows: list[dict[str, Any]]) -> float:
    assert _ORIGINAL_PROFIT is not None
    return float(_ORIGINAL_PROFIT([row for row in rows if not _is_result_only(row)]))


def _backfill_signal_state() -> int:
    path = _brain_state_path()
    try:
        payload = json.loads(path.read_text("utf-8"))
    except Exception:
        return 0
    signals = payload.get("signals") if isinstance(payload, dict) else None
    if not isinstance(signals, dict):
        return 0
    changed = 0
    for row in signals.values():
        if not isinstance(row, dict) or not bool(row.get("telegram_sent")):
            continue
        if _persist_entry(row, int(row.get("telegram_delivery_count") or 1)):
            changed += 1
    return changed


def _menu_reconcile() -> int:
    changed = int(_ORIGINAL_MENU_RECONCILE() if _ORIGINAL_MENU_RECONCILE is not None else 0)
    from . import multi_menu
    from .journal import load_signal_journal
    from .multi_delivery import pending_result_notifications
    from .multi_journal import settle_multi_journal
    from .providers.flashscore import FlashscoreProvider

    path = _journal_path()
    rows = [
        row for row in load_signal_journal(path)
        if _is_result_only(row) and str(row.get("result") or "") == TRACKING_RESULT
    ]
    ids = {str(row.get("match_id") or "") for row in rows if str(row.get("match_id") or "")}
    if not ids:
        return changed
    provider = FlashscoreProvider()
    try:
        states = provider.event_states(ids)
    except Exception:
        return changed

    for row in rows:
        mid = str(row.get("match_id") or "")
        state = states.get(mid)
        if not isinstance(state, dict):
            continue
        entry_score = _score(row.get("score"))
        current_score = [int(state.get("home_score") or 0), int(state.get("away_score") or 0)]
        family = str(row.get("market_family") or "")
        finished = bool(state.get("is_finished")) or str(state.get("coarse_status") or "") == "3"
        need_timeline = family == "first_half_total" or finished or sum(current_score) > sum(entry_score)
        timeline: list[dict[str, Any]] = []
        if need_timeline:
            try:
                timeline = provider.fetch_goal_timeline(mid)
            except Exception:
                timeline = []
        record = multi_menu._synthetic_record(row, state, timeline)
        settled = settle_multi_journal(record, path)
        changed += len(settled)
        result_rows = pending_result_notifications(path, match_id=mid)
        if result_rows:
            _emit_results(record, result_rows, journal_path=path)
    return changed


def install_brain_journal_results() -> None:
    global _INSTALLED, _ORIGINAL_MARK, _ORIGINAL_SETTLE_ENTRY, _ORIGINAL_FINISH_ROW
    global _ORIGINAL_RECONCILE_FIRST_HALF, _ORIGINAL_EMIT_RESULTS, _ORIGINAL_MENU_RECONCILE
    global _ORIGINAL_STATS_LINE, _ORIGINAL_ROI, _ORIGINAL_PROFIT
    if _INSTALLED:
        return
    with _LOCK:
        if _INSTALLED:
            return
        from . import brain_primary_mode as brain
        from . import multi_journal
        from . import multi_menu
        from . import multi_runtime

        _ORIGINAL_MARK = brain._mark_brain_sent
        _ORIGINAL_SETTLE_ENTRY = multi_journal.settle_entry
        _ORIGINAL_FINISH_ROW = multi_journal._finish_row
        _ORIGINAL_RECONCILE_FIRST_HALF = multi_journal._reconcile_final_first_half
        _ORIGINAL_EMIT_RESULTS = multi_runtime.emit_multi_results
        _ORIGINAL_MENU_RECONCILE = multi_menu.reconcile_pending
        _ORIGINAL_STATS_LINE = multi_menu._stats_line
        _ORIGINAL_ROI = multi_menu._roi
        _ORIGINAL_PROFIT = multi_menu._profit

        brain._mark_brain_sent = _mark_and_journal
        multi_journal.settle_entry = _settle_tracking
        multi_journal._finish_row = _finish_result_only
        multi_journal._reconcile_final_first_half = _reconcile_first_half
        multi_runtime.emit_multi_results = _emit_results
        multi_menu.reconcile_pending = _menu_reconcile
        multi_menu._stats_line = _stats_line
        multi_menu._roi = _roi
        multi_menu._profit = _profit
        _INSTALLED = True

        backfilled = _backfill_signal_state()
        print(
            f"GOOL_BRAIN_JOURNAL installed result_cards=on accounting=result_only backfilled={backfilled}",
            flush=True,
        )


__all__ = ["install_brain_journal_results"]
