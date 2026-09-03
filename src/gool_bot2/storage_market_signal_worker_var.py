from __future__ import annotations

from typing import Any

from . import signal_worker as core
from . import signal_worker_all as base
from . import storage_market_signal_worker as app
from . import storage_signal_worker as storage
from . import telegram_in_game_guard as _telegram_in_game_guard  # noqa: F401
from . import first_half_product as _first_half_product  # noqa: F401
from . import journal_reconcile_all as _journal_reconcile_all  # noqa: F401
from .multi_runtime import observe_multi_shadow
from .var_settlement_guard import clear_provisional, confirmed_win

_ORIG_SETTLE = base._settle_pending
_ORIG_TWO = base._settle_two_more
_ORIG_PROCESS = storage.StorageCardAllMatchSignalWorker._process


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


def _process_with_multi(self, record: dict[str, Any]):
    emitted = _ORIG_PROCESS(self, record)
    try:
        observe_multi_shadow(self, record)
    except Exception as exc:
        print(f"GOOL_MULTI_SHADOW_ERROR {type(exc).__name__}:{exc}", flush=True)
    return emitted


base._settle_pending = _guard_main
base._settle_two_more = _guard_two
storage.StorageCardAllMatchSignalWorker._process = _process_with_multi


def main() -> None:
    app.main()


if __name__ == "__main__":
    main()
