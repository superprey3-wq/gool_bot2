from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from . import bot_menu, telegram
from .journal import load_signal_journal, save_signal_journal
from .providers.flashscore import FlashscoreProvider

_ORIG_FORCE = telegram._force_reconcile_pending


def _age_hours(row: dict[str, Any]) -> float:
    raw = str(row.get("created_at") or row.get("captured_at") or "").strip()
    if not raw:
        return 999.0
    try:
        dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return max(0.0, (datetime.now(timezone.utc) - dt.astimezone(timezone.utc)).total_seconds() / 3600.0)
    except Exception:
        return 999.0


def _timeline_final_score(timeline: list[dict[str, Any]], entry: list[Any]) -> list[int]:
    try:
        home = int(entry[0] or 0); away = int(entry[1] or 0)
    except Exception:
        home = away = 0
    for goal in timeline:
        score = goal.get("score") or []
        try:
            if len(score) >= 2:
                home = max(home, int(score[0] or 0)); away = max(away, int(score[1] or 0))
        except Exception:
            continue
    return [home, away]


def _winning_minute(head: str, row: dict[str, Any], timeline: list[dict[str, Any]]) -> int | None:
    entry = row.get("score") or [0, 0]
    try:
        eh = int(entry[0] or 0); ea = int(entry[1] or 0)
    except Exception:
        eh = ea = 0
    side = str(row.get("selected_side") or "")
    entry_minute = int(row.get("minute") or 0)
    for goal in sorted(timeline, key=lambda g: int(g.get("minute") or 0)):
        minute = int(goal.get("minute") or 0)
        if minute < entry_minute:
            continue
        score = goal.get("score") or []
        try:
            hs = int(score[0] or 0); aws = int(score[1] or 0)
        except Exception:
            continue
        if head == "both_teams_to_score" and hs > 0 and aws > 0:
            return minute
        if head == "team_to_score" and ((side == "home" and hs > eh) or (side == "away" and aws > ea)):
            return minute
        if head == "goal_before_ht" and minute <= 45 and hs + aws > eh + ea:
            return minute
    return None


def _reconcile_shadow(main_journal: Path) -> int:
    path = bot_menu._experiment_journal(main_journal)
    rows = load_signal_journal(path)
    pending = [
        r for r in rows
        if str(r.get("result") or "pending").lower() == "pending"
        and str(r.get("head") or "") in bot_menu.EXPERIMENT_HEADS
    ]
    ids = {str(r.get("match_id") or "") for r in pending if str(r.get("match_id") or "")}
    if not ids:
        return 0
    try:
        provider = FlashscoreProvider(); states = provider.event_states(ids)
    except Exception as exc:
        print(f"JOURNAL_RECONCILE_SHADOW_STATE_ERROR {type(exc).__name__}:{exc}", flush=True)
        provider = FlashscoreProvider(); states = {}
    stale_hours = float(__import__("os").getenv("PENDING_FORCE_CLOSE_HOURS", "4"))
    timelines: dict[str, list[dict[str, Any]]] = {}
    changed = 0; now = datetime.now(timezone.utc).isoformat()
    for row in pending:
        mid = str(row.get("match_id") or ""); state = states.get(mid) or {}
        finished = bool(state.get("is_finished")); stale = _age_hours(row) >= stale_hours
        # Do not settle a live win here: the normal worker/VAR guard owns live wins.
        # This fallback exists only to prevent completed/stale matches from hanging pending.
        if not finished and not stale:
            continue
        if mid not in timelines:
            try: timelines[mid] = provider.fetch_goal_timeline(mid)
            except Exception: timelines[mid] = []
        timeline = timelines[mid]; entry = row.get("score") or [0, 0]
        tscore = _timeline_final_score(timeline, entry)
        try:
            sh = int(state.get("home_score") or 0); sa = int(state.get("away_score") or 0)
        except Exception:
            sh = sa = 0
        hs = max(sh, tscore[0]); aws = max(sa, tscore[1])
        head = str(row.get("head") or ""); side = str(row.get("selected_side") or "")
        try: eh = int(entry[0] or 0); ea = int(entry[1] or 0)
        except Exception: eh = ea = 0
        if head == "both_teams_to_score":
            won = hs > 0 and aws > 0
        else:
            won = (side == "home" and hs > eh) or (side == "away" and aws > ea)
        win_minute = _winning_minute(head, row, timeline) if won else None
        row.update({
            "result": "won" if won else "lost",
            "settled_at": now,
            "settled_minute": int(win_minute or state.get("minute") or 90),
            "settled_score": [hs, aws],
            "settlement_source": "flashscore_finished_reconcile" if finished else "flashscore_stale_reconcile",
        })
        changed += 1
    if changed:
        save_signal_journal(path, rows)
        print(f"JOURNAL_RECONCILE_SHADOW closed={changed} pending={len(pending)}", flush=True)
    return changed


def _reconcile_first_half(main_journal: Path) -> int:
    rows = load_signal_journal(main_journal)
    pending = [r for r in rows if str(r.get("result") or "pending").lower() == "pending" and str(r.get("head") or "") == "goal_before_ht"]
    ids = {str(r.get("match_id") or "") for r in pending if str(r.get("match_id") or "")}
    if not ids:
        return 0
    try:
        provider = FlashscoreProvider(); states = provider.event_states(ids)
    except Exception as exc:
        print(f"JOURNAL_RECONCILE_1T_STATE_ERROR {type(exc).__name__}:{exc}", flush=True)
        provider = FlashscoreProvider(); states = {}
    stale_hours = float(__import__("os").getenv("PENDING_FORCE_CLOSE_HOURS", "4"))
    timelines: dict[str, list[dict[str, Any]]] = {}
    changed = 0; now = datetime.now(timezone.utc).isoformat()
    for row in pending:
        mid = str(row.get("match_id") or ""); state = states.get(mid) or {}
        minute = int(state.get("minute") or 0); finished = bool(state.get("is_finished"))
        halftime_passed = bool(state.get("is_halftime")) or minute >= 46 or finished or _age_hours(row) >= stale_hours
        if not halftime_passed:
            continue
        if mid not in timelines:
            try: timelines[mid] = provider.fetch_goal_timeline(mid)
            except Exception: timelines[mid] = []
        timeline = timelines[mid]; win_minute = _winning_minute("goal_before_ht", row, timeline)
        entry = row.get("score") or [0, 0]
        # Reconstruct the score at the end of the first half only; second-half goals must not win this market.
        try: hs = int(entry[0] or 0); aws = int(entry[1] or 0)
        except Exception: hs = aws = 0
        for goal in sorted(timeline, key=lambda g: int(g.get("minute") or 0)):
            gm = int(goal.get("minute") or 0)
            if gm > 45: continue
            score = goal.get("score") or []
            try:
                if len(score) >= 2: hs = max(hs, int(score[0] or 0)); aws = max(aws, int(score[1] or 0))
            except Exception: continue
        row.update({
            "result": "won" if win_minute is not None else "lost",
            "settled_at": now,
            "settled_minute": int(win_minute or 45),
            "settled_score": [hs, aws],
            "settlement_source": "flashscore_first_half_reconcile",
        })
        changed += 1
    if changed:
        save_signal_journal(main_journal, rows)
        print(f"JOURNAL_RECONCILE_1T closed={changed} pending={len(pending)}", flush=True)
    return changed


def _force_reconcile_all(journal_path: Path) -> int:
    changed = int(_ORIG_FORCE(journal_path) or 0)
    changed += _reconcile_first_half(journal_path)
    changed += _reconcile_shadow(journal_path)
    return changed


telegram._force_reconcile_pending = _force_reconcile_all
