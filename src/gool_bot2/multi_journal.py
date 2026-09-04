from __future__ import annotations

import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .journal import load_signal_journal, save_signal_journal
from .multi_bank import apply_settlement_fields, attach_entry_fields, ensure_bank_fields
from .multi_router import RouterDecision
from .signal_cards import flashscore_meta, stats_snapshot


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _score(record: dict[str, Any]) -> tuple[int, int]:
    match = record.get("match") or {}
    return int(match.get("home_score") or 0), int(match.get("away_score") or 0)


def _timeline(record: dict[str, Any]) -> list[dict[str, Any]]:
    return list(
        ((((record.get("providers") or {}).get("flashscore") or {}).get("meta") or {}).get("goal_timeline") or [])
    )


def _line_from_key(key: str) -> float | None:
    if ":" not in str(key):
        return None
    try:
        return float(str(key).rsplit(":", 1)[1])
    except (TypeError, ValueError):
        return None


def _source(winner: Any) -> str:
    if not bool(getattr(winner, "expert_passed", True)) and bool(getattr(winner, "market_override", False)):
        return "MARKET_OVERRIDE"
    if not bool(getattr(winner, "expert_passed", True)) and bool(getattr(winner, "value_override", False)):
        return "VALUE_OVERRIDE"
    return "GOOL"


def _entry_key(match_id: str, score: tuple[int, int], market_key: str) -> str:
    return f"{match_id}:{score[0]}-{score[1]}:{market_key}"


def entry_from_decision(
    record: dict[str, Any],
    decision: RouterDecision,
    experts: dict[str, Any],
    *,
    data_quality: float,
) -> dict[str, Any] | None:
    winner = decision.winner
    if decision.status != "BET" or winner is None:
        return None
    match = record.get("match") or {}
    match_id = str(match.get("flashscore_event_id") or "")
    if not match_id:
        return None
    score = _score(record)
    mode = "active" if str(os.getenv("GOOL_MULTI_TELEGRAM_MODE", "shadow")).strip().lower() == "active" else "shadow"
    return {
        "created_at": _now(),
        "mode": mode,
        "head": "multi",
        "match_id": match_id,
        "home": match.get("home"),
        "away": match.get("away"),
        "league": match.get("league"),
        "minute": int(match.get("minute") or 0),
        "score": [score[0], score[1]],
        "entry_key": _entry_key(match_id, score, winner.key),
        "market_key": winner.key,
        "market_family": winner.family,
        "market": winner.label,
        "strategy": winner.strategy,
        "odd": round(float(winner.odd), 4),
        "probability": round(float(winner.model_probability), 6),
        "push_probability": round(float(winner.push_probability), 6),
        "market_probability": None if winner.market_probability is None else round(float(winner.market_probability), 6),
        "rating": round(float(winner.rating), 2),
        "expected_roi": round(float(winner.expected_roi), 6),
        "value_edge_pp": round(float(winner.value_edge_pp), 3),
        "market_pressure_pp": round(float(winner.market_pressure_pp), 3),
        "market_level": winner.market_level,
        "signal_source": _source(winner),
        "source": winner.source,
        "data_quality": round(float(data_quality), 4),
        "reason": decision.reason,
        "reason_tags": list(winner.reason_tags),
        "expert_passed": bool(winner.expert_passed),
        "expert_blocks": list(winner.expert_blocks),
        "experts": experts,
        "alternatives": [row.to_dict() for row in decision.alternatives],
        "flashscore_meta": flashscore_meta(record),
        "stats_snapshot": stats_snapshot(record),
        "cards": dict(record.get("cards") or {}),
        "result": "pending",
        "profit_units": None,
    }


def _timeline_hit(
    row: dict[str, Any],
    timeline: list[dict[str, Any]],
    *,
    max_minute: int | None = None,
) -> tuple[int, list[int]] | None:
    family = str(row.get("market_family") or "")
    key = str(row.get("market_key") or "")
    line = _line_from_key(key)
    for goal in sorted(timeline, key=lambda item: int(item.get("minute") or 0)):
        event_type = str(goal.get("event_type") or "").strip().lower()
        if event_type and event_type != "goal":
            continue
        minute = int(goal.get("minute") or 0)
        if max_minute is not None:
            period = str(goal.get("period") or "").strip().upper()
            if max_minute == 45 and period:
                # 45+N is still first half even though its normalized numeric
                # minute is greater than 45. New Flashscore timeline rows carry
                # the period explicitly so stoppage-time goals are not lost.
                if period != "1H":
                    continue
            elif minute > max_minute:
                continue
        score = goal.get("score") or []
        try:
            hs, aws = int(score[0] or 0), int(score[1] or 0)
        except Exception:
            continue
        won = False
        if family in {"match_total", "first_half_total"} and line is not None:
            won = hs + aws > line
        elif family == "team_total" and line is not None:
            goals = hs if key.startswith("home_total:") else aws
            won = goals > line
        elif family == "btts":
            won = hs > 0 and aws > 0
        if won:
            return minute, [hs, aws]
    return None


def _half_time_score(timeline: list[dict[str, Any]]) -> list[int] | None:
    seen = False
    score = [0, 0]
    for goal in sorted(timeline, key=lambda item: int(item.get("minute") or 0)):
        event_type = str(goal.get("event_type") or "").strip().lower()
        if event_type and event_type != "goal":
            continue
        minute = int(goal.get("minute") or 0)
        period = str(goal.get("period") or "").strip().upper()
        if period:
            if period != "1H":
                continue
        elif minute > 45:
            continue
        raw = goal.get("score") or []
        try:
            score = [int(raw[0] or 0), int(raw[1] or 0)]
            seen = True
        except Exception:
            continue
    return score if seen else ([0, 0] if timeline else None)


def _finish_row(
    row: dict[str, Any],
    *,
    result: str,
    minute: int,
    score: list[int],
    reason: str,
) -> None:
    odd = float(row.get("odd") or 0.0)
    profit = odd - 1.0 if result == "won" else (-1.0 if result == "lost" else 0.0)
    row.update(
        {
            "result": result,
            "profit_units": round(profit, 4),
            "settled_at": _now(),
            "settled_minute": int(minute),
            "settled_score": [int(score[0]), int(score[1])],
            "settlement_source": reason,
        }
    )


def settle_entry(row: dict[str, Any], record: dict[str, Any]) -> bool:
    if str(row.get("result") or "pending").lower() != "pending":
        return False
    match = record.get("match") or {}
    if str(row.get("match_id") or "") != str(match.get("flashscore_event_id") or ""):
        return False

    minute = int(match.get("minute") or 0)
    finished = bool(match.get("is_finished"))
    is_halftime = bool(match.get("is_halftime"))
    hs, aws = _score(record)
    timeline = _timeline(record)
    family = str(row.get("market_family") or "")
    key = str(row.get("market_key") or "")
    line = _line_from_key(key)

    if family == "first_half_total":
        hit = _timeline_hit(row, timeline, max_minute=45)
        if hit is not None:
            hit_minute, hit_score = hit
            _finish_row(row, result="won", minute=hit_minute, score=hit_score, reason="flashscore_first_half_timeline")
            return True
        if not is_halftime and minute <= 45 and not finished:
            if line is not None and hs + aws > line:
                _finish_row(row, result="won", minute=minute, score=[hs, aws], reason="live_first_half_score")
                return True
            return False

        ht_score = _half_time_score(timeline)
        if ht_score is None:
            if is_halftime:
                ht_score = [hs, aws]
            else:
                entry_score = list(row.get("score") or [0, 0])
                if [hs, aws] == [int(entry_score[0] or 0), int(entry_score[1] or 0)]:
                    _finish_row(
                        row,
                        result="lost",
                        minute=45,
                        score=[hs, aws],
                        reason="no_first_half_goal_score_unchanged",
                    )
                    return True
                _finish_row(
                    row,
                    result="void",
                    minute=45,
                    score=[int(entry_score[0] or 0), int(entry_score[1] or 0)],
                    reason="half_time_score_unavailable",
                )
                return True
        result = "won" if line is not None and sum(ht_score) > line else "lost"
        _finish_row(row, result=result, minute=45, score=ht_score, reason="flashscore_first_half_close")
        return True

    hit = _timeline_hit(row, timeline)
    if hit is not None:
        hit_minute, hit_score = hit
        _finish_row(row, result="won", minute=hit_minute, score=hit_score, reason="flashscore_goal_timeline")
        return True

    won_now = False
    if family == "match_total" and line is not None:
        won_now = hs + aws > line
    elif family == "team_total" and line is not None:
        team_goals = hs if key.startswith("home_total:") else aws
        won_now = team_goals > line
    elif family == "btts":
        won_now = hs > 0 and aws > 0
    if won_now:
        _finish_row(row, result="won", minute=minute, score=[hs, aws], reason="live_score")
        return True
    if finished:
        _finish_row(row, result="lost", minute=90, score=[hs, aws], reason="flashscore_finished")
        return True
    return False


def settle_multi_journal(record: dict[str, Any], journal_path: Path) -> list[dict[str, Any]]:
    rows = load_signal_journal(journal_path)
    bank_changed = ensure_bank_fields(rows, journal_path)
    changed: list[dict[str, Any]] = []
    for row in rows:
        if settle_entry(row, record):
            row["settled_stats_snapshot"] = stats_snapshot(record)
            row["settled_cards"] = dict(record.get("cards") or {})
            if not row.get("flashscore_meta"):
                row["flashscore_meta"] = flashscore_meta(record)
            apply_settlement_fields(row)
            changed.append(dict(row))
    if changed or bank_changed:
        save_signal_journal(journal_path, rows)
    return changed


def record_multi_entry(
    record: dict[str, Any],
    decision: RouterDecision,
    experts: dict[str, Any],
    journal_path: Path,
    *,
    data_quality: float,
) -> dict[str, Any] | None:
    candidate = entry_from_decision(record, decision, experts, data_quality=data_quality)
    if candidate is None:
        return None
    rows = load_signal_journal(journal_path)
    match_id = str(candidate.get("match_id") or "")
    if any(
        str(row.get("match_id") or "") == match_id
        and str(row.get("result") or "pending").lower() == "pending"
        for row in rows
    ):
        return None
    if any(str(row.get("entry_key") or "") == str(candidate.get("entry_key") or "") for row in rows):
        return None
    attach_entry_fields(candidate, rows, journal_path)
    rows.append(candidate)
    save_signal_journal(journal_path, rows)
    return candidate


def sync_multi_journal(
    record: dict[str, Any],
    decision: RouterDecision,
    experts: dict[str, Any],
    journal_path: Path,
    *,
    data_quality: float,
) -> tuple[list[dict[str, Any]], dict[str, Any] | None]:
    settled = settle_multi_journal(record, journal_path)
    created = record_multi_entry(
        record,
        decision,
        experts,
        journal_path,
        data_quality=data_quality,
    )
    return settled, created
