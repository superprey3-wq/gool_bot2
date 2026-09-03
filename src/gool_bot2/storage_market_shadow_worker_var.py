from __future__ import annotations

from typing import Any

from . import shadow_market_worker as worker
from . import storage_market_shadow_worker as app
from .var_settlement_guard import clear_provisional, confirmed_win

_ORIG_SETTLE = worker._settle


def _raw_won(row: dict[str, Any], hs: int, aws: int) -> bool:
    head = str(row.get("head") or "")
    entry = row.get("score") or [0, 0]
    if head == "both_teams_to_score":
        return hs > 0 and aws > 0
    if head == "team_to_score":
        side = str(row.get("selected_side") or "")
        return (side == "home" and hs > int(entry[0] or 0)) or (side == "away" and aws > int(entry[1] or 0))
    return False


def _guard_settle(record: dict[str, Any], rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    match = record.get("match") or {}
    mid = str(match.get("flashscore_event_id") or "")
    minute = int(match.get("minute") or 0)
    hs = int(match.get("home_score") or 0)
    aws = int(match.get("away_score") or 0)
    finished = bool(match.get("is_finished"))
    returned = _ORIG_SETTLE(record, rows)
    kept: list[dict[str, Any]] = []

    for item in returned:
        if str(item.get("result") or "") != "won" or finished:
            clear_provisional(item)
            kept.append(item)
            continue
        raw = _raw_won(item, hs, aws)
        if confirmed_win(item, raw_won=raw, minute=minute, home_score=hs, away_score=aws):
            kept.append(item)
        else:
            item["result"] = "pending"
            for key in ("settled_at", "settled_minute", "settled_score"):
                item.pop(key, None)
            print(f"VAR_PROVISIONAL_WIN match={mid} head={item.get('head')} score={hs}:{aws} minute={minute}", flush=True)

    for row in rows:
        if str(row.get("match_id") or "") != mid or str(row.get("result") or "pending").lower() != "pending":
            continue
        if not _raw_won(row, hs, aws):
            clear_provisional(row)
    return kept


worker._settle = _guard_settle


def main() -> None:
    app.main()


if __name__ == "__main__":
    main()
