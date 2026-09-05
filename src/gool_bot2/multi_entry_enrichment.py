from __future__ import annotations

from pathlib import Path
from typing import Any

from .journal import load_signal_journal, save_signal_journal
from .multi_match_intelligence import calibration_snapshot
from .multi_public_metrics import brief_selection_reason, confidence_snapshot


def _entry_timing_snapshot(record: dict[str, Any]) -> dict[str, Any]:
    match = record.get("match") or {}
    flash_meta = (((record.get("providers") or {}).get("flashscore") or {}).get("meta") or {})
    timeline = flash_meta.get("goal_timeline") or []
    incidents = flash_meta.get("incident_timeline") or []
    last_goal = None
    for row in timeline:
        try:
            minute = int(float(row.get("minute")))
        except (TypeError, ValueError, AttributeError):
            continue
        last_goal = minute if last_goal is None else max(last_goal, minute)
    minute_now = int(match.get("minute") or 0)
    last_incident = incidents[-1] if incidents else None
    return {
        "version": 1,
        "captured_at": record.get("captured_at"),
        "goal_timeline_source": flash_meta.get("goal_timeline_source") or ((record.get("consensus") or {}).get("goal_timeline_source")),
        "goal_timeline_candidates": dict(flash_meta.get("goal_timeline_candidates") or {}),
        "last_goal_minute": last_goal,
        "minutes_since_last_goal": None if last_goal is None else max(0, minute_now - int(last_goal)),
        "last_incident": None if not isinstance(last_incident, dict) else {
            "minute": last_incident.get("minute"),
            "event_type": last_incident.get("event_type"),
            "side": last_incident.get("side"),
        },
        "provider_freshness": dict(record.get("provider_freshness") or {}),
    }


def enrich_multi_entry(
    journal_path: Path,
    entry: dict[str, Any] | None,
    record: dict[str, Any],
    decision: Any,
    experts: dict[str, Any],
    *,
    data_quality: float,
) -> dict[str, Any] | None:
    if entry is None or getattr(decision, "winner", None) is None:
        return entry

    winner = decision.winner
    metrics = confidence_snapshot(
        record,
        winner,
        experts,
        data_quality=data_quality,
    )
    calibration_input = {**entry, "model_probability": entry.get("probability")}
    payload = {
        **metrics,
        "selection_reason": brief_selection_reason(decision, winner),
        "confidence_formula_version": 2,
        "live_momentum_snapshot": dict(record.get("live_momentum") or {}),
        "xbet_live_1x2": dict(record.get("xbet_live_1x2") or {}),
        "matchbook_exchange": dict(record.get("matchbook_exchange") or {}),
        "match_intelligence": dict(record.get("match_intelligence") or {}),
        "calibration": calibration_snapshot(calibration_input, record),
        # Shadow-first timing input for Entry Quality analysis. Together with
        # `match_intelligence.score_epoch`, this distinguishes pressure that
        # existed before the current score from football created afterwards.
        "entry_timing": _entry_timing_snapshot(record),
    }
    if str(getattr(winner, "source", "") or "").startswith("1xbet:autonomous_steam"):
        payload["signal_source"] = "STEAM_OVERRIDE"
    elif bool(getattr(winner, "market_override", False)) and not bool(getattr(winner, "expert_passed", True)):
        payload["signal_source"] = "MARKET_CONFIRM"
    else:
        payload["signal_source"] = "GOOL_STATE"

    entry.update(payload)

    entry_key = str(entry.get("entry_key") or "")
    if not entry_key:
        return entry

    rows = load_signal_journal(journal_path)
    changed = False
    for row in rows:
        if str(row.get("entry_key") or "") != entry_key:
            continue
        row.update(payload)
        changed = True
        break
    if changed:
        save_signal_journal(journal_path, rows)
    return entry
