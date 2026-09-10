from __future__ import annotations

import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .journal import load_signal_journal, save_signal_journal


def _parse_dt(value: Any) -> datetime | None:
    raw = str(value or "").strip()
    if not raw:
        return None
    try:
        dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except Exception:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _age_hours(row: dict[str, Any]) -> float | None:
    dt = _parse_dt(row.get("created_at") or row.get("telegram_sent_at"))
    if dt is None:
        return None
    return max(0.0, (datetime.now(timezone.utc) - dt.astimezone(timezone.utc)).total_seconds() / 3600.0)


def _minimum_age_hours() -> float:
    try:
        return max(2.5, float(os.getenv("GOOL_ORPHAN_PENDING_CLOSE_HOURS", "4")))
    except (TypeError, ValueError):
        return 4.0


def _score(value: Any) -> list[int]:
    try:
        if isinstance(value, (list, tuple)) and len(value) >= 2:
            return [int(value[0] or 0), int(value[1] or 0)]
    except Exception:
        pass
    return [0, 0]


def _timeline_final_score(timeline: list[dict[str, Any]]) -> list[int] | None:
    final: list[int] | None = None
    for event in sorted(timeline, key=lambda row: int(row.get("minute") or 0)):
        if str(event.get("event_type") or "goal").lower() != "goal":
            continue
        score = _score(event.get("score"))
        if final is None or sum(score) >= sum(final):
            final = score
    return final


def _void_unverifiable(path: Path, match_id: str) -> int:
    """Remove a very old orphan from In Game without inventing a win/loss."""
    from . import multi_journal
    from .multi_bank import apply_settlement_fields

    rows = load_signal_journal(path)
    changed = 0
    now = datetime.now(timezone.utc).isoformat()
    for row in rows:
        if str(row.get("match_id") or "") != match_id:
            continue
        if str(row.get("result") or "pending").lower() not in {"pending", "tracking"}:
            continue
        score = _score(row.get("score"))
        multi_journal._finish_row(
            row,
            result="void",
            minute=90,
            score=score,
            reason="flashscore_orphan_unverifiable",
        )
        apply_settlement_fields(row)
        row["result_notification_pending"] = False
        row["result_notification_suppressed"] = True
        row["result_notification_suppression_reason"] = "orphan_unverifiable_no_replay"
        row["result_notification_suppressed_at"] = now
        changed += 1
    if changed:
        save_signal_journal(path, rows)
    return changed


def reconcile_orphaned_pending(journal_path: Path) -> int:
    """Settle journal entries that disappeared from Flashscore's master feed.

    Membership in ``В игре`` still comes from the journal. Flashscore is used only
    to settle it. Normally ``event_states`` supplies FT/HT and the standard
    reconciler closes the row. If an event has already aged out of the master feed,
    a row older than four hours is reconstructed from its goal timeline. When the
    timeline itself is unavailable or inconsistent we mark VOID rather than invent
    a win/loss, so the row leaves ``В игре`` without corrupting statistics.
    """
    from .multi_journal import settle_multi_journal
    from .providers.flashscore import FlashscoreProvider

    path = Path(journal_path)
    rows = load_signal_journal(path)
    threshold = _minimum_age_hours()
    candidates = [
        row for row in rows
        if str(row.get("result") or "pending").lower() in {"pending", "tracking"}
        and bool(row.get("telegram_sent"))
        and (_age_hours(row) is not None and float(_age_hours(row) or 0.0) >= threshold)
        and str(row.get("match_id") or "")
    ]
    ids = {str(row.get("match_id") or "") for row in candidates}
    if not ids:
        return 0

    provider = FlashscoreProvider()
    try:
        states = provider.event_states(ids)
    except Exception as exc:
        print(f"GOOL_ORPHAN_STATE_ERROR {type(exc).__name__}:{exc}", flush=True)
        return 0

    orphan_ids = sorted(mid for mid in ids if mid not in states)
    changed = 0
    for mid in orphan_ids:
        try:
            timeline = provider.fetch_goal_timeline(mid)
        except Exception:
            timeline = []
        final_score = _timeline_final_score(timeline)
        event_rows = [row for row in candidates if str(row.get("match_id") or "") == mid]
        max_entry_total = max((sum(_score(row.get("score"))) for row in event_rows), default=0)

        if final_score is None or sum(final_score) < max_entry_total:
            closed = _void_unverifiable(path, mid)
            changed += closed
            if closed:
                print(f"GOOL_ORPHAN_RECONCILE match={mid} closed={closed} result=void reason=timeline_unavailable", flush=True)
            continue

        sample = event_rows[0]
        record = {
            "match": {
                "flashscore_event_id": mid,
                "home": sample.get("home"),
                "away": sample.get("away"),
                "league": sample.get("league"),
                "minute": 90,
                "home_score": int(final_score[0]),
                "away_score": int(final_score[1]),
                "is_halftime": False,
                "is_finished": True,
                "status_code": "3",
            },
            "providers": {"flashscore": {"meta": {"goal_timeline": timeline}}},
        }
        settled = settle_multi_journal(record, path)
        changed += len(settled)
        if settled:
            print(
                f"GOOL_ORPHAN_RECONCILE match={mid} closed={len(settled)} "
                f"score={final_score[0]}:{final_score[1]} source=timeline",
                flush=True,
            )
    return changed


__all__ = ["reconcile_orphaned_pending"]
