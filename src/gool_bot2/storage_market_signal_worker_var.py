from __future__ import annotations

import time
from typing import Any

from . import signal_worker as core
from . import signal_worker_all as base
from . import signal_worker_all_cards as cards
from . import storage_market_signal_worker as app
from . import storage_signal_worker as storage
from . import telegram as telegram_mod
from . import telegram_in_game_guard as _telegram_in_game_guard  # noqa: F401
from . import first_half_product as _first_half_product  # noqa: F401
from . import journal_reconcile_all as _journal_reconcile_all  # noqa: F401
from .multi_bank import daily_report_due_date, mark_daily_report_sent, render_daily_bank_report
from .multi_late_refresh import refresh_late_another_goal_model
from .multi_menu import journal_path as multi_journal_path, reconcile_pending
from .multi_model_capture import ensure_model_snapshot_capture
from .multi_money_flow import maybe_emit_money_flow
from .multi_product import install_multi_product
from .multi_runtime import observe_multi_shadow
from .multi_telegram import silence_legacy_telegram
from .var_settlement_guard import clear_provisional, confirmed_win

_ORIG_SETTLE = base._settle_pending
_ORIG_TWO = base._settle_two_more
_ORIG_PROCESS = storage.StorageCardAllMatchSignalWorker._process
_ORIG_POLL = base.poll_telegram_updates
_ORIG_ENSURE_MODEL = cards.CardAllMatchSignalWorker._ensure_model
_LAST_BANK_REPORT_ATTEMPT = 0.0


def _find_row(journal: list[dict[str, Any]], returned: dict[str, Any]) -> dict[str, Any] | None:
    for row in journal:
        if str(row.get("match_id") or "") != str(returned.get("match_id") or ""):
            continue
        if str(row.get("head") or "") != str(returned.get("head") or ""):
            continue
        if str(row.get("created_at") or "") == str(returned.get("created_at") or ""):
            return row
        if int(row.get("minute") or 0) == int(returned.get("minute") or 0):
            return row
    return None


def _revert(row: dict[str, Any]) -> None:
    row["result"] = "pending"
    for key in ("settled_at", "settled_minute", "settled_score", "settlement_source"):
        row.pop(key, None)


def _guard_main(record: dict[str, Any], journal: list[dict[str, Any]]) -> list[dict[str, Any]]:
    match = record.get("match") or {}
    minute = int(match.get("minute") or 0)
    finished = bool(match.get("is_finished"))
    hs, aws = base._reconciled_score(record)
    returned = _ORIG_SETTLE(record, journal)
    kept: list[dict[str, Any]] = []

    for item in returned:
        row = _find_row(journal, item) or item
        if str(item.get("result") or "") != "won" or finished:
            clear_provisional(row)
            kept.append(item)
            continue
        raw = core._is_won(str(row.get("head") or ""), row.get("score") or [0, 0], minute, hs, aws)
        if confirmed_win(row, raw_won=raw, minute=minute, home_score=hs, away_score=aws):
            kept.append(dict(row))
        else:
            _revert(row)
            print(f"VAR_PROVISIONAL_WIN match={row.get('match_id')} head={row.get('head')} score={hs}:{aws} minute={minute}", flush=True)

    for row in journal:
        if str(row.get("match_id") or "") != str(match.get("flashscore_event_id") or ""):
            continue
        if str(row.get("result") or "pending").lower() != "pending":
            continue
        raw = core._is_won(str(row.get("head") or ""), row.get("score") or [0, 0], minute, hs, aws)
        if not raw:
            clear_provisional(row)
    return kept


def _guard_two(record: dict[str, Any], journal: list[dict[str, Any]]) -> list[dict[str, Any]]:
    match = record.get("match") or {}
    minute = int(match.get("minute") or 0)
    finished = bool(match.get("is_finished"))
    hs, aws = base._reconciled_score(record)
    total = hs + aws
    returned = _ORIG_TWO(record, journal)
    kept: list[dict[str, Any]] = []
    for item in returned:
        row = _find_row(journal, item) or item
        if str(item.get("result") or "") != "won" or finished:
            clear_provisional(row)
            kept.append(item)
            continue
        entry = row.get("score") or [0, 0]
        raw = total >= int(entry[0] or 0) + int(entry[1] or 0) + 2
        if confirmed_win(row, raw_won=raw, minute=minute, home_score=hs, away_score=aws):
            kept.append(dict(row))
        else:
            _revert(row)
            print(f"VAR_PROVISIONAL_WIN match={row.get('match_id')} head=two_more_goals score={hs}:{aws} minute={minute}", flush=True)
    for row in journal:
        if str(row.get("match_id") or "") != str(match.get("flashscore_event_id") or "") or str(row.get("head") or "") != "two_more_goals":
            continue
        if str(row.get("result") or "pending").lower() != "pending":
            continue
        entry = row.get("score") or [0, 0]
        raw = total >= int(entry[0] or 0) + int(entry[1] or 0) + 2
        if not raw:
            clear_provisional(row)
    return kept


def _ensure_model_with_multi_snapshot(self):
    return ensure_model_snapshot_capture(self, _ORIG_ENSURE_MODEL)


def _process_with_multi(self, record: dict[str, Any]):
    market_only = str(record.get("runtime_scope") or "").strip().lower() == "market_only"
    emitted = 0

    # Market heartbeat must not pay for the hidden legacy analyzer. Full detail
    # snapshots still feed it for model diagnostics used by ordinary GOOL.
    if not market_only:
        try:
            with silence_legacy_telegram():
                emitted = _ORIG_PROCESS(self, record)
        except Exception as exc:
            print(f"GOOL_LEGACY_ANALYZER_ERROR {type(exc).__name__}:{exc}", flush=True)

        try:
            refresh_late_another_goal_model(self, record)
        except Exception as exc:
            print(f"GOOL_LATE_REFRESH_ERROR {type(exc).__name__}:{exc}", flush=True)

    # Each public system has its own failure domain. A malformed football record
    # may not suppress exchange flow, and a Matchbook error may not suppress GOOL.
    try:
        observe_multi_shadow(self, record)
    except Exception as exc:
        print(f"GOOL_MULTI_RUNTIME_ERROR {type(exc).__name__}:{exc}", flush=True)

    try:
        maybe_emit_money_flow(record)
    except Exception as exc:
        print(f"GOOL_MONEY_FLOW_RUNTIME_ERROR {type(exc).__name__}:{exc}", flush=True)
    return emitted


def _poll_with_multi_bank(journal_path, offset: int = 0, timeout: int = 0):
    global _LAST_BANK_REPORT_ATTEMPT
    next_offset, actions = _ORIG_POLL(journal_path, offset=offset, timeout=timeout)
    try:
        multi_path = multi_journal_path()
        due = daily_report_due_date(multi_path)
        now_mono = time.monotonic()
        if due is not None and now_mono - _LAST_BANK_REPORT_ATTEMPT >= 60.0:
            _LAST_BANK_REPORT_ATTEMPT = now_mono
            reconcile_pending()
            text = render_daily_bank_report(multi_path, report_date=due)
            sent = telegram_mod.broadcast(text)
            if sent > 0:
                mark_daily_report_sent(multi_path, due)
                print(f"GOOL_MULTI_BANK_REPORT date={due.isoformat()} sent={sent}", flush=True)
                actions += sent
            else:
                print(f"GOOL_MULTI_BANK_REPORT_RETRY date={due.isoformat()} sent=0", flush=True)
    except Exception as exc:
        print(f"GOOL_MULTI_BANK_REPORT_ERROR {type(exc).__name__}:{exc}", flush=True)
    return next_offset, actions


base._settle_pending = _guard_main
base._settle_two_more = _guard_two
cards.CardAllMatchSignalWorker._ensure_model = _ensure_model_with_multi_snapshot
storage.StorageCardAllMatchSignalWorker._process = _process_with_multi
base.poll_telegram_updates = _poll_with_multi_bank
install_multi_product()


def main() -> None:
    app.main()


if __name__ == "__main__":
    main()
