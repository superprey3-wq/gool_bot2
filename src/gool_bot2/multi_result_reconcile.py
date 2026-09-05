from __future__ import annotations

from pathlib import Path
from typing import Any

from .journal import load_signal_journal
from .multi_journal import settle_multi_journal
from .providers.flashscore import (
    FINISHED_COARSE_STATUS,
    FIRST_HALF_STATUS,
    HALFTIME_STATUS,
    SECOND_HALF_STATUS,
    FlashscoreProvider,
)


_SUSPECT_FIRST_HALF_SOURCES = {
    "flashscore_first_half_timeline",
    "live_first_half_score",
    "flashscore_first_half_close",
}


def _needs_recheck(row: dict[str, Any]) -> bool:
    return bool(
        str(row.get("market_family") or "") == "first_half_total"
        and str(row.get("result") or "").lower() in {"won", "lost"}
        and str(row.get("settlement_source") or "") in _SUSPECT_FIRST_HALF_SOURCES
    )


def _synthetic_record(
    row: dict[str, Any],
    state: dict[str, Any],
    timeline: list[dict[str, Any]],
) -> dict[str, Any]:
    status = str(state.get("status_code") or "")
    finished = bool(state.get("is_finished")) or str(state.get("coarse_status") or "") == FINISHED_COARSE_STATUS
    if finished:
        minute = 90
    elif status == HALFTIME_STATUS:
        minute = 45
    elif status == SECOND_HALF_STATUS:
        minute = 46
    elif status == FIRST_HALF_STATUS:
        minute = max(1, int(row.get("minute") or 1))
    else:
        minute = int(row.get("minute") or 0)
    return {
        "match": {
            "flashscore_event_id": str(row.get("match_id") or ""),
            "home": row.get("home"),
            "away": row.get("away"),
            "league": row.get("league"),
            "minute": minute,
            "home_score": int(state.get("home_score") or 0),
            "away_score": int(state.get("away_score") or 0),
            "is_halftime": status == HALFTIME_STATUS,
            "is_finished": finished,
            "status_code": status,
        },
        "providers": {
            "flashscore": {
                "meta": {
                    "goal_timeline": timeline,
                    "endpoints": {
                        "master": {
                            "status_code": status,
                            "score": [
                                int(state.get("home_score") or 0),
                                int(state.get("away_score") or 0),
                            ],
                        }
                    },
                }
            }
        },
    }


def reconcile_finalized_first_half(journal_path: Path) -> int:
    """Recheck old 1H settlements that could have been finalized before VAR.

    The old Multi path could mark a first-half total WON from the first timeline
    appearance of a goal. If Flashscore later cancelled that goal, the row stayed
    final. Recheck only those legacy settlement sources against the authoritative
    Flashscore state. New VAR-safe settlements are not touched.
    """
    rows = load_signal_journal(journal_path)
    candidates = [row for row in rows if _needs_recheck(row)]
    ids = {
        str(row.get("match_id") or "")
        for row in candidates
        if str(row.get("match_id") or "")
    }
    if not ids:
        return 0

    provider = FlashscoreProvider()
    try:
        states = provider.event_states(ids)
    except Exception as exc:
        print(f"GOOL_MULTI_RESULT_RECHECK_STATE_ERROR {type(exc).__name__}:{exc}", flush=True)
        return 0

    changed = 0
    processed: set[str] = set()
    for row in candidates:
        mid = str(row.get("match_id") or "")
        if not mid or mid in processed:
            continue
        state = states.get(mid)
        if not state:
            continue
        status = str(state.get("status_code") or "")
        finished = bool(state.get("is_finished")) or str(state.get("coarse_status") or "") == FINISHED_COARSE_STATUS
        if status == FIRST_HALF_STATUS and not finished:
            continue

        timeline: list[dict[str, Any]] = []
        try:
            timeline = provider.fetch_goal_timeline(mid)
        except Exception:
            timeline = []

        corrected = settle_multi_journal(_synthetic_record(row, state, timeline), journal_path)
        if corrected:
            changed += len(corrected)
            print(
                f"GOOL_MULTI_RESULT_RECHECK match={mid} corrected={len(corrected)} "
                f"score={state.get('home_score', 0)}:{state.get('away_score', 0)} status={status or '-'}",
                flush=True,
            )
        processed.add(mid)
    return changed
