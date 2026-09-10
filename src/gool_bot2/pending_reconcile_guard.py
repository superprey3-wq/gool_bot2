from __future__ import annotations

import os
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


_LOCK = threading.RLock()
_INSTALLED = False
_ORIGINAL_RECONCILE: Any = None


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
    return max(
        0.0,
        (datetime.now(timezone.utc) - dt.astimezone(timezone.utc)).total_seconds() / 3600.0,
    )


def _timeline_score(timeline: list[dict[str, Any]], entry_score: list[Any]) -> list[int] | None:
    """Return a final score only when Flashscore returned actual goal evidence."""
    if not timeline:
        return None
    try:
        home, away = int(entry_score[0] or 0), int(entry_score[1] or 0)
    except Exception:
        home = away = 0
    seen = False
    for goal in sorted(timeline, key=lambda item: int(item.get("minute") or 0)):
        if str(goal.get("event_type") or "goal").lower() != "goal":
            continue
        score = goal.get("score") or []
        try:
            if len(score) < 2:
                continue
            gh, ga = int(score[0] or 0), int(score[1] or 0)
        except Exception:
            continue
        if gh < home or ga < away:
            continue
        home, away = gh, ga
        seen = True
    return [home, away] if seen else None


def _void_unverifiable(path: Path, entry_key: str) -> bool:
    from .journal import journal_transaction, load_signal_journal, save_signal_journal
    from . import multi_bank
    from . import multi_journal

    with journal_transaction(path):
        rows = load_signal_journal(path)
        target = next(
            (
                row
                for row in rows
                if str(row.get("entry_key") or "") == entry_key
                and str(row.get("result") or "pending").lower() == "pending"
            ),
            None,
        )
        if target is None:
            return False
        score = list(target.get("score") or [0, 0])
        try:
            score = [int(score[0] or 0), int(score[1] or 0)]
        except Exception:
            score = [0, 0]
        multi_journal._finish_row(
            target,
            result="void",
            minute=90,
            score=score,
            reason="flashscore_state_missing_unverifiable_void",
        )
        multi_bank.apply_settlement_fields(target)
        # This is an archival cleanup, not a newly observed result. Do not emit
        # a misleading old VOID card after a provider outage/restart.
        target["result_notification_pending"] = False
        target["result_notification_suppressed"] = True
        target["result_notification_suppressed_at"] = datetime.now(timezone.utc).isoformat()
        target["result_notification_suppression_reason"] = "stale_unverifiable_cleanup"
        save_signal_journal(path, rows)
        return True


def _reconcile_missing_states() -> int:
    """Settle/void journal rows that disappeared from Flashscore master state.

    Absence from ``event_states`` never means LOSS. After the match has had ample
    time to finish, confirmed goal timeline evidence may safely settle the row.
    If no authoritative evidence is retrievable for a much older row, it becomes
    VOID rather than hanging in "В игре" forever or fabricating a loss.
    """
    from .journal import load_signal_journal
    from . import multi_menu
    from .multi_journal import settle_multi_journal
    from .providers.flashscore import FINISHED_COARSE_STATUS, FlashscoreProvider

    path = multi_menu.journal_path()
    rows = [
        row
        for row in load_signal_journal(path)
        if str(row.get("result") or "pending").lower() == "pending"
        and str(row.get("mode") or "active").lower() == "active"
        and str(row.get("entry_key") or "")
    ]
    if not rows:
        return 0

    try:
        evidence_hours = max(2.0, float(os.getenv("GOOL_PENDING_MISSING_STATE_RECHECK_HOURS", "4")))
    except (TypeError, ValueError):
        evidence_hours = 4.0
    try:
        void_hours = max(evidence_hours, float(os.getenv("GOOL_PENDING_UNVERIFIABLE_VOID_HOURS", "8")))
    except (TypeError, ValueError):
        void_hours = 8.0

    old = [row for row in rows if _age_hours(row) >= evidence_hours]
    if not old:
        return 0
    ids = {str(row.get("match_id") or "") for row in old if str(row.get("match_id") or "")}
    provider = FlashscoreProvider()
    try:
        states = provider.event_states(ids)
    except Exception as exc:
        print(f"GOOL_PENDING_RECHECK_STATE_ERROR {type(exc).__name__}:{exc}", flush=True)
        states = {}

    changed = 0
    for row in old:
        mid = str(row.get("match_id") or "")
        # If the event is still returned, the normal reconciler owns it.
        if mid and isinstance(states.get(mid), dict):
            continue

        timeline: list[dict[str, Any]] = []
        if mid:
            try:
                timeline = provider.fetch_goal_timeline(mid)
            except Exception:
                timeline = []
        score = _timeline_score(timeline, list(row.get("score") or [0, 0]))
        if score is not None:
            synthetic_state = {
                "home_score": score[0],
                "away_score": score[1],
                "is_finished": True,
                "coarse_status": FINISHED_COARSE_STATUS,
                "status_code": "",
            }
            settled = settle_multi_journal(
                multi_menu._synthetic_record(row, synthetic_state, timeline),
                path,
            )
            if settled:
                changed += len(settled)
                print(
                    f"GOOL_PENDING_RECONCILED match={mid} via=timeline rows={len(settled)} "
                    f"score={score[0]}:{score[1]}",
                    flush=True,
                )
                continue

        if _age_hours(row) >= void_hours and _void_unverifiable(path, str(row.get("entry_key") or "")):
            changed += 1
            print(
                f"GOOL_PENDING_VOID match={mid} entry={row.get('entry_key')} "
                f"age_h={_age_hours(row):.1f} reason=unverifiable",
                flush=True,
            )
    return changed


def reconcile_pending_guarded() -> int:
    changed = int(_ORIGINAL_RECONCILE() or 0) if _ORIGINAL_RECONCILE is not None else 0
    return changed + _reconcile_missing_states()


def install_pending_reconcile_guard() -> None:
    global _INSTALLED, _ORIGINAL_RECONCILE
    if _INSTALLED:
        return
    with _LOCK:
        if _INSTALLED:
            return
        from . import multi_menu

        _ORIGINAL_RECONCILE = multi_menu.reconcile_pending
        multi_menu.reconcile_pending = reconcile_pending_guarded
        _INSTALLED = True
        print(
            "GOOL_PENDING_RECONCILE installed missing_state=timeline_then_void "
            "void_is_not_loss=1",
            flush=True,
        )


__all__ = ["install_pending_reconcile_guard", "reconcile_pending_guarded"]
