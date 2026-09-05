from __future__ import annotations

import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .journal import load_signal_journal, save_signal_journal
from .multi_bank import apply_settlement_fields, attach_entry_fields, ensure_bank_fields
from .multi_router import RouterDecision
from .providers.flashscore import FIRST_HALF_STATUS
from .signal_cards import flashscore_meta, stats_snapshot
from .var_settlement_guard import clear_provisional, confirmed_win


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _score(record: dict[str, Any]) -> tuple[int, int]:
    match = record.get("match") or {}
    return int(match.get("home_score") or 0), int(match.get("away_score") or 0)


def _timeline(record: dict[str, Any]) -> list[dict[str, Any]]:
    return list(
        ((((record.get("providers") or {}).get("flashscore") or {}).get("meta") or {}).get("goal_timeline") or [])
    )


def _status_code(record: dict[str, Any]) -> str:
    match = record.get("match") or {}
    direct = str(match.get("status_code") or "").strip()
    if direct:
        return direct
    meta = (((record.get("providers") or {}).get("flashscore") or {}).get("meta") or {})
    master = ((meta.get("endpoints") or {}).get("master") or {})
    return str(master.get("status_code") or "").strip()


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


def _timeline_final_score(timeline: list[dict[str, Any]]) -> list[int]:
    score = [0, 0]
    for goal in sorted(timeline, key=lambda item: int(item.get("minute") or 0)):
        raw = goal.get("score") or []
        try:
            if len(raw) >= 2:
                score = [int(raw[0] or 0), int(raw[1] or 0)]
        except Exception:
            continue
    return score


def _wins_at_score(row: dict[str, Any], hs: int, aws: int) -> bool:
    family = str(row.get("market_family") or "")
    key = str(row.get("market_key") or "")
    line = _line_from_key(key)
    if family in {"match_total", "first_half_total"} and line is not None:
        return hs + aws > line
    if family == "team_total" and line is not None:
        return (hs if key.startswith("home_total:") else aws) > line
    if family == "btts":
        return hs > 0 and aws > 0
    return False


def _authoritative_first_half_score(record: dict[str, Any]) -> tuple[list[int] | None, str]:
    match = record.get("match") or {}
    hs, aws = _score(record)
    if bool(match.get("is_halftime")):
        return [hs, aws], "flashscore_halftime_master"

    # While Flashscore still says 1H, never use a goal timeline as a final
    # settlement source. A provisional goal can still be cancelled by VAR.
    if _status_code(record) == FIRST_HALF_STATUS:
        return None, "first_half_still_live"

    timeline = _timeline(record)
    if not timeline:
        return None, "first_half_timeline_missing"

    # After the break, accept the first-half reconstruction only when the whole
    # timeline exactly explains the current authoritative Flashscore score. This
    # rejects stale/provisional 2:2 timelines after the master score rolls back
    # to 2:1, which is the failure mode seen in Wuhan Three Towns - Qingdao.
    if _timeline_final_score(timeline) != [hs, aws]:
        return None, "first_half_timeline_score_desync"
    ht_score = _half_time_score(timeline)
    if ht_score is None:
        return None, "first_half_score_unavailable"
    return ht_score, "flashscore_verified_first_half_timeline"


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


def _reconcile_final_first_half(row: dict[str, Any], record: dict[str, Any]) -> bool:
    if str(row.get("market_family") or "") != "first_half_total":
        return False
    current = str(row.get("result") or "pending").lower()
    if current == "pending":
        return False
    match = record.get("match") or {}
    if str(row.get("match_id") or "") != str(match.get("flashscore_event_id") or ""):
        return False

    ht_score, source = _authoritative_first_half_score(record)
    line = _line_from_key(str(row.get("market_key") or ""))
    if ht_score is None or line is None:
        return False
    desired = "won" if sum(ht_score) > line else "lost"
    if desired == current:
        return False

    previous = {
        "result": current,
        "settled_at": row.get("settled_at"),
        "settled_minute": row.get("settled_minute"),
        "settled_score": list(row.get("settled_score") or []),
        "settlement_source": row.get("settlement_source"),
    }
    _finish_row(
        row,
        result=desired,
        minute=45,
        score=ht_score,
        reason=f"{source}_var_correction",
    )
    row["settlement_corrected"] = True
    row["settlement_correction"] = previous
    row["settlement_correction_reason"] = "authoritative_first_half_score"
    clear_provisional(row)
    return True


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
    family = str(row.get("market_family") or "")
    key = str(row.get("market_key") or "")
    line = _line_from_key(key)

    if family == "first_half_total":
        # Never publish a first-half win while 1H is still live. Flashscore can
        # show a provisional goal for tens of seconds before a VAR/offside
        # rollback. The final 1H product is settled from the authoritative score
        # at halftime, not from the first appearance of a goal incident.
        status = _status_code(record)
        if not is_halftime and not finished and (status == FIRST_HALF_STATUS or (not status and minute <= 45)):
            clear_provisional(row)
            return False

        ht_score, source = _authoritative_first_half_score(record)
        if ht_score is None:
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
                reason=source or "half_time_score_unavailable",
            )
            return True

        result = "won" if line is not None and sum(ht_score) > line else "lost"
        _finish_row(row, result=result, minute=45, score=ht_score, reason=source)
        clear_provisional(row)
        return True

    # All full-match/team/BTTS products are decided from the authoritative
    # current score, not from an incident timeline. A live win must survive the
    # shared VAR confirmation window before it can settle and produce a card.
    won_now = _wins_at_score(row, hs, aws)
    if won_now:
        if finished or confirmed_win(
            row,
            raw_won=True,
            minute=minute,
            home_score=hs,
            away_score=aws,
        ):
            _finish_row(
                row,
                result="won",
                minute=minute,
                score=[hs, aws],
                reason="flashscore_finished" if finished else "flashscore_var_confirmed_live_score",
            )
            clear_provisional(row)
            return True
        return False

    clear_provisional(row)
    if finished:
        _finish_row(row, result="lost", minute=90, score=[hs, aws], reason="flashscore_finished")
        return True
    return False


def settle_multi_journal(record: dict[str, Any], journal_path: Path) -> list[dict[str, Any]]:
    rows = load_signal_journal(journal_path)
    bank_changed = ensure_bank_fields(rows, journal_path)
    changed: list[dict[str, Any]] = []
    for row in rows:
        corrected = _reconcile_final_first_half(row, record)
        settled = False if corrected else settle_entry(row, record)
        if corrected or settled:
            row["settled_stats_snapshot"] = stats_snapshot(record)
            row["settled_cards"] = dict(record.get("cards") or {})
            if not row.get("flashscore_meta"):
                row["flashscore_meta"] = flashscore_meta(record)
            apply_settlement_fields(row)
            row["result_notification_pending"] = True
            row["result_notification_created_at"] = _now()
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
