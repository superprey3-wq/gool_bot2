from __future__ import annotations

import os
import sys
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


_LOCK = threading.RLock()
_INSTALLED = False
_ORIGINAL_PENDING: Any = None


def _parse_dt(value: Any) -> datetime | None:
    raw = str(value or "").strip()
    if not raw:
        return None
    try:
        dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except Exception:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _max_replay_age_hours() -> float:
    try:
        return max(0.25, float(os.getenv("GOOL_RESULT_REPLAY_MAX_SIGNAL_AGE_HOURS", "4")))
    except (TypeError, ValueError):
        return 4.0


def _signal_age_hours(row: dict[str, Any]) -> float | None:
    created = _parse_dt(row.get("created_at") or row.get("telegram_sent_at"))
    if created is None:
        return None
    return max(0.0, (datetime.now(timezone.utc) - created.astimezone(timezone.utc)).total_seconds() / 3600.0)


def _strict_pre_send_reason(record: dict[str, Any], decision: Any, entry: dict[str, Any]) -> str | None:
    """Fail closed when an outgoing Brain signal cannot be revalidated as LIVE."""
    from . import brain_card_restore as card

    match = record.get("match") or {}
    match_id = str(entry.get("match_id") or match.get("flashscore_event_id") or "")
    state = card._fresh_flashscore_state(match_id)
    if not state:
        return "fresh_flashscore_state_unavailable"

    coarse = str(state.get("coarse_status") or "")
    if bool(state.get("is_finished")) or coarse == "3":
        return "match_finished"
    if ("is_live" in state or coarse) and not bool(state.get("is_live")) and coarse != "2":
        return f"match_not_live_{coarse or 'unknown'}"
    if bool(state.get("score_conflict")):
        return "flashscore_master_timeline_score_conflict"

    expected = card._score_pair(getattr(decision, "score", None)) or card._score_pair(entry.get("score")) or card._score_pair(match)
    fresh = card._score_pair(state)
    if expected is not None and fresh is not None and fresh != expected:
        return f"score_changed_{expected[0]}-{expected[1]}_to_{fresh[0]}-{fresh[1]}"
    return None


def _suppress_stale_result_replays(journal_path: Path) -> int:
    """Clear result notifications that are historical replays, not fresh results."""
    from .journal import load_signal_journal, save_signal_journal
    from . import multi_delivery as delivery

    changed = 0
    now = datetime.now(timezone.utc).isoformat()
    max_age = _max_replay_age_hours()
    with delivery._journal_delivery_lock(journal_path):
        rows = load_signal_journal(journal_path)
        for row in rows:
            if not bool(row.get("result_notification_pending")):
                continue
            age = _signal_age_hours(row)
            explicitly_suppressed = bool(row.get("result_notification_suppressed"))
            too_old = age is not None and age > max_age
            if not (explicitly_suppressed or too_old):
                continue
            row["result_notification_pending"] = False
            row["result_notification_suppressed"] = True
            row["result_notification_suppressed_at"] = now
            row["result_notification_suppression_reason"] = (
                "historical_backfill" if explicitly_suppressed else f"signal_age_gt_{max_age:g}h"
            )
            delivery._clear_claim(row)
            changed += 1
        if changed:
            save_signal_journal(journal_path, rows)
    return changed


def _pending_without_stale_replay(
    journal_path: Path,
    *,
    match_id: str | None = None,
) -> list[dict[str, Any]]:
    _suppress_stale_result_replays(journal_path)
    if _ORIGINAL_PENDING is None:
        return []
    return _ORIGINAL_PENDING(journal_path, match_id=match_id)


def install_stale_replay_guard() -> None:
    """Install stale-signal and historical-result protection once at startup."""
    global _INSTALLED, _ORIGINAL_PENDING
    if _INSTALLED:
        return
    with _LOCK:
        if _INSTALLED:
            return

        from . import brain_card_restore as card
        from . import brain_journal_tracking as tracking
        from . import multi_delivery as delivery

        _ORIGINAL_PENDING = delivery.pending_result_notifications
        card._pre_send_stale_reason = _strict_pre_send_reason
        delivery.pending_result_notifications = _pending_without_stale_replay

        # New Brain signals are persisted directly after successful Telegram
        # delivery. Re-importing historical signal_only state after a restart can
        # resurrect finished matches, so production startup backfill is disabled.
        tracking._backfill_recent_signal_only = lambda: 0

        runtime = sys.modules.get("gool_bot2.multi_runtime")
        if runtime is not None:
            setattr(runtime, "pending_result_notifications", _pending_without_stale_replay)

        _INSTALLED = True
        print(
            "GOOL_STALE_REPLAY_GUARD installed presend_flashscore=master+timeline-required "
            "historical_backfill=off result_replay_max_age_h=4",
            flush=True,
        )


__all__ = ["install_stale_replay_guard"]
