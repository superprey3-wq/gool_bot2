from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .journal import append_analysis
from .match_context import provider_count, provider_pair, xg_or_proxy_pair
from .multi_router import RouterDecision, analyze_multi_match


def _pair_total(record: dict[str, Any], key: str) -> float | None:
    try:
        home, away = provider_pair(record, key)
    except Exception:
        return None
    if home is None or away is None:
        return None
    return float(home) + float(away)


def _timeline_context(record: dict[str, Any]) -> dict[str, Any]:
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
        "source": flash_meta.get("goal_timeline_source") or ((record.get("consensus") or {}).get("goal_timeline_source")),
        "provider_candidates": dict(flash_meta.get("goal_timeline_candidates") or {}),
        "goal_count": len(timeline),
        "last_goal_minute": last_goal,
        "minutes_since_last_goal": None if last_goal is None else max(0, minute_now - int(last_goal)),
        "incident_count": len(incidents),
        "last_incident": None if not isinstance(last_incident, dict) else {
            "minute": last_incident.get("minute"),
            "event_type": last_incident.get("event_type"),
            "side": last_incident.get("side"),
        },
    }


def context_snapshot(record: dict[str, Any]) -> dict[str, Any]:
    prematch = record.get("prematch_context") or {}
    momentum = record.get("live_momentum") or {}
    try:
        xh, xa, xg_source, xg_evidence = xg_or_proxy_pair(record)
    except Exception:
        xh = xa = None
        xg_source = "unavailable"
        xg_evidence = 0
    return {
        "prematch": {
            "available": bool(prematch),
            "source": prematch.get("source"),
            "sources": list(prematch.get("sources") or []),
            "home_recent": len(prematch.get("home_recent") or []),
            "away_recent": len(prematch.get("away_recent") or []),
            "home_at_home": len(prematch.get("home_at_home") or []),
            "away_away": len(prematch.get("away_away") or []),
            "h2h": len(prematch.get("h2h") or []),
        },
        "live": {
            "providers": provider_count(record),
            "xg_source": xg_source,
            "xg_evidence": xg_evidence,
            "xg_home": xh,
            "xg_away": xa,
            "xg_total": None if xh is None or xa is None else float(xh) + float(xa),
            "shots_total": _pair_total(record, "shots"),
            "sot_total": _pair_total(record, "shots_on_target"),
            "big_chances_total": _pair_total(record, "big_chances"),
            "dangerous_attacks_total": _pair_total(record, "dangerous_attacks"),
            "xg_5m": momentum.get("xg_total_last_5m"),
            "shots_5m": momentum.get("shots_total_last_5m"),
            "sot_5m": momentum.get("sot_total_last_5m"),
            "xg_10m": momentum.get("xg_total_last_10m"),
            "shots_10m": momentum.get("shots_total_last_10m"),
            "sot_10m": momentum.get("sot_total_last_10m"),
        },
        # Observation-only timing data. It is deliberately not part of
        # data_quality or any BET/WAIT gate yet; first collect a clean sample.
        "provider_freshness": dict(record.get("provider_freshness") or {}),
        "timeline": _timeline_context(record),
        "prefilter": dict(record.get("prefilter") or {}),
    }


def _market_age_seconds(market_row: dict[str, Any] | None) -> float | None:
    raw = None if not market_row else market_row.get("captured_at")
    if not raw:
        return None
    try:
        dt = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return max(0.0, (datetime.now(timezone.utc) - dt.astimezone(timezone.utc)).total_seconds())
    except Exception:
        return None


def _line_row(rows: list[dict[str, Any]], line: float) -> dict[str, Any] | None:
    for row in rows or []:
        try:
            if abs(float(row.get("line")) - float(line)) < 1e-9:
                return row
        except (TypeError, ValueError):
            continue
    return None


def _target(rows: list[dict[str, Any]], line: float, label: str) -> dict[str, Any]:
    row = _line_row(rows, line)
    odd = None
    if row and row.get("over") is not None:
        try:
            odd = float(row.get("over"))
        except (TypeError, ValueError):
            odd = None
    return {"label": label, "line": line, "available": odd is not None, "odd": odd}


def market_snapshot(match: dict[str, Any], market_row: dict[str, Any] | None) -> dict[str, Any]:
    """Compact 1xBet state for explaining why a Multi candidate was not built."""
    hs = int(match.get("home_score") or 0)
    aws = int(match.get("away_score") or 0)
    minute = int(match.get("minute") or 0)
    total = hs + aws
    if not market_row:
        return {"available": False, "reason": "xbet_match_not_mapped_or_state_missing", "targets": {}}

    markets = market_row.get("markets") or {}
    btts = markets.get("btts") or {}
    targets: dict[str, dict[str, Any]] = {
        "another_goal": _target(list(markets.get("match_total") or []), total + 0.5, f"ТБ {total + 0.5:g}"),
        "two_more_goals": _target(list(markets.get("match_total") or []), total + 1.5, f"ТБ {total + 1.5:g}"),
        "home_goal": _target(list(markets.get("home_total") or []), hs + 0.5, f"ИТБ1 {hs + 0.5:g}"),
        "away_goal": _target(list(markets.get("away_total") or []), aws + 0.5, f"ИТБ2 {aws + 0.5:g}"),
        "btts": {
            "label": "ОЗ — Да",
            "available": btts.get("yes") is not None,
            "odd": None if btts.get("yes") is None else float(btts.get("yes")),
        },
    }
    if 0 < minute <= 45:
        targets["goal_before_ht"] = _target(
            list(markets.get("first_half_total") or []), total + 0.5, f"1Т ТБ {total + 0.5:g}"
        )

    market_score = (
        int(market_row.get("score_home") or market_row.get("flashscore_score_home") or 0),
        int(market_row.get("score_away") or market_row.get("flashscore_score_away") or 0),
    )
    return {
        "available": True,
        "xbet_event_id": market_row.get("xbet_event_id"),
        "captured_at": market_row.get("captured_at"),
        "age_seconds": _market_age_seconds(market_row),
        "score": [market_score[0], market_score[1]],
        "score_desync": bool(market_score != (hs, aws) or market_row.get("score_desync")),
        "timeline_score_desync": bool(market_row.get("timeline_score_desync")),
        "score_verified": market_row.get("score_verified"),
        "repricing_guard": bool(market_row.get("repricing_guard")),
        "repricing_guard_reason": market_row.get("repricing_guard_reason"),
        "seconds_since_event_change": market_row.get("seconds_since_event_change"),
        "seconds_since_xbet_score_change": market_row.get("seconds_since_xbet_score_change"),
        "targets": targets,
    }


def decision_snapshot(
    record: dict[str, Any],
    decision: RouterDecision,
    experts: dict[str, Any],
    *,
    data_quality: float,
    market_row: dict[str, Any] | None = None,
) -> dict[str, Any]:
    match = record.get("match") or {}
    return {
        "created_at": datetime.now(timezone.utc).isoformat(),
        "mode": "shadow",
        "match_id": str(match.get("flashscore_event_id") or ""),
        "home": match.get("home"),
        "away": match.get("away"),
        "league": match.get("league"),
        "minute": int(match.get("minute") or 0),
        "score": [int(match.get("home_score") or 0), int(match.get("away_score") or 0)],
        "data_quality": float(data_quality),
        "context": context_snapshot(record),
        "market": market_snapshot(match, market_row),
        "experts": experts,
        "router": decision.to_dict(),
    }


def append_shadow_snapshot(path: Path, snapshot: dict[str, Any]) -> None:
    # Reuse the global bounded JSONL writer. Multi diagnostics are disposable
    # and must never be able to fill a small production disk.
    append_analysis(path, snapshot)


def analyze_and_record(
    record: dict[str, Any],
    market_row: dict[str, Any] | None,
    experts: dict[str, Any],
    analysis_path: Path,
    *,
    data_quality: float = 1.0,
) -> RouterDecision:
    """Run GOOL MULTI and append the full per-snapshot analysis row.

    The persistent bet journal is intentionally separate: analysis contains
    every BET/WAIT decision, while the journal contains only unique selected
    BEST BET entries and their settlements.
    """
    decision = analyze_multi_match(record.get("match") or {}, market_row, experts, data_quality=data_quality)
    append_shadow_snapshot(
        analysis_path,
        decision_snapshot(record, decision, experts, data_quality=data_quality, market_row=market_row),
    )
    return decision
