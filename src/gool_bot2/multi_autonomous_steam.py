from __future__ import annotations

import os
from datetime import datetime, timezone
from typing import Any

from .multi_router import MarketCandidate, RouterDecision
from .value_bet_policy import ABSOLUTE_MIN_BET_ODD


def _f(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        return default


def _i(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        return default


def _enabled() -> bool:
    return str(os.getenv("GOOL_MULTI_AUTONOMOUS_STEAM", "1")).strip().lower() not in {"0", "false", "no", "off"}


def _age(row: dict[str, Any] | None) -> float | None:
    raw = None if not row else row.get("captured_at")
    if not raw:
        return None
    try:
        dt = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return max(0.0, (datetime.now(timezone.utc) - dt.astimezone(timezone.utc)).total_seconds())
    except Exception:
        return None


def _line(rows: list[dict[str, Any]], line: float) -> dict[str, Any] | None:
    for row in rows or []:
        try:
            if abs(float(row.get("line")) - line) < 1e-9:
                return dict(row)
        except (TypeError, ValueError):
            continue
    return None


def _fair(odd: Any, opposite: Any) -> float | None:
    try:
        a_odd = float(odd)
    except (TypeError, ValueError):
        return None
    if a_odd <= 1.0:
        return None
    a = 1.0 / a_odd
    try:
        b_odd = float(opposite)
    except (TypeError, ValueError):
        b_odd = 0.0
    if b_odd <= 1.0:
        return a
    b = 1.0 / b_odd
    return a / (a + b) if a + b else None


def _strength(delta: float, moves: int, quality: float) -> float:
    md = _f("XBET_AUTONOMOUS_STEAM_MIN_DELTA_PP", 8.0)
    mm = _i("XBET_AUTONOMOUS_STEAM_MIN_ONE_WAY_MOVES", 3)
    mq = _f("XBET_AUTONOMOUS_STEAM_MIN_DATA_QUALITY", 0.45)
    score = 70 + min(16, max(0.0, delta - md) * 2) + min(8, max(0, moves - mm) * 4) + min(5, max(0.0, quality - mq) * 10)
    return round(max(70.0, min(99.0, score)), 1)


def _candidate(
    *,
    row: dict[str, Any],
    key: str,
    pkey: str,
    family: str,
    label: str,
    strategy: str,
    odd: Any,
    opposite: Any,
    goals: int,
    correlation: str,
    age: float,
    quality: float,
) -> MarketCandidate | None:
    try:
        current = float(odd)
    except (TypeError, ValueError):
        return None
    min_odd = max(ABSOLUTE_MIN_BET_ODD, _f("XBET_AUTONOMOUS_STEAM_MIN_ODD", 1.40))
    max_odd = max(min_odd, _f("XBET_AUTONOMOUS_STEAM_MAX_ODD", 2.50))
    if not min_odd <= current <= max_odd:
        return None
    pressure = (row.get("pressure") or {}).get(pkey) or {}
    try:
        delta = float(pressure.get("prob_delta_pp") or 0.0)
        moves = int(pressure.get("one_way_moves") or 0)
        old = float(pressure.get("old_odd"))
    except (TypeError, ValueError):
        return None
    if delta < _f("XBET_AUTONOMOUS_STEAM_MIN_DELTA_PP", 8.0):
        return None
    if moves < _i("XBET_AUTONOMOUS_STEAM_MIN_ONE_WAY_MOVES", 3):
        return None
    if old <= current + max(0.0, _f("XBET_AUTONOMOUS_STEAM_MIN_ODD_DROP", 0.03)):
        return None
    strength = _strength(delta, moves, quality)
    return MarketCandidate(
        key=key,
        family=family,
        label=label,
        odd=current,
        model_probability=strength / 100.0,
        push_probability=0.0,
        market_probability=_fair(current, opposite),
        goals_to_win=goals,
        correlation_key=correlation,
        strategy=strategy,
        source="1xbet:autonomous_steam",
        expert_passed=False,
        expert_blocks=["autonomous_steam_bypass"],
        market_pressure_pp=delta,
        market_level="AUTONOMOUS_STEAM",
        market_override=True,
        value_override=False,
        override_reason=f"AUTONOMOUS STEAM · Δp={delta:.1f} п.п. · импульсов={moves} · {old:.2f}→{current:.2f}",
        market_age_seconds=age,
        data_quality=max(0.0, min(1.0, float(quality))),
        rating=strength,
        expected_roi=0.0,
        value_edge_pp=0.0,
        eligible=True,
        reason_tags=["autonomous_steam", "market_steam", "confidence_metric"],
    )


def build_autonomous_steam_candidates(
    record: dict[str, Any],
    market_row: dict[str, Any] | None,
    *,
    data_quality: float,
) -> list[MarketCandidate]:
    match = record.get("match") or {}
    if not _enabled() or not market_row or bool(match.get("is_finished")):
        return []
    if market_row.get("repricing_guard") or market_row.get("score_desync") or market_row.get("timeline_score_desync"):
        return []
    if market_row.get("score_verified") is False:
        return []
    hs, aws = int(match.get("home_score") or 0), int(match.get("away_score") or 0)
    if (int(market_row.get("score_home") or 0), int(market_row.get("score_away") or 0)) != (hs, aws):
        return []
    minute = int(match.get("minute") or 0)
    if minute < max(1, _i("XBET_AUTONOMOUS_STEAM_MIN_MINUTE", 10)):
        return []
    if data_quality < max(0.0, min(1.0, _f("XBET_AUTONOMOUS_STEAM_MIN_DATA_QUALITY", 0.45))):
        return []
    age = _age(market_row)
    if age is None or age > max(5.0, _f("XBET_AUTONOMOUS_STEAM_MAX_AGE_SECONDS", 30.0)):
        return []
    markets, total, out = market_row.get("markets") or {}, hs + aws, []

    def add(candidate: MarketCandidate | None) -> None:
        if candidate is not None:
            out.append(candidate)

    if minute <= _i("XBET_AUTONOMOUS_STEAM_ANOTHER_GOAL_MAX_MINUTE", 85):
        line = total + 0.5
        target = _line(list(markets.get("match_total") or []), line)
        if target and target.get("over") is not None:
            add(_candidate(
                row=market_row,
                key=f"match_total:{line:g}",
                pkey=f"match_total:{line}",
                family="match_total",
                label=f"ТБ {line:g}",
                strategy="steam_another_goal",
                odd=target.get("over"),
                opposite=target.get("under"),
                goals=1,
                correlation="any_next_goal",
                age=age,
                quality=data_quality,
            ))
    if minute <= _i("XBET_AUTONOMOUS_STEAM_TWO_GOALS_MAX_MINUTE", 60):
        line = total + 1.5
        target = _line(list(markets.get("match_total") or []), line)
        if target and target.get("over") is not None:
            add(_candidate(
                row=market_row,
                key=f"match_total:{line:g}",
                pkey=f"match_total:{line}",
                family="match_total",
                label=f"ТБ {line:g}",
                strategy="steam_two_more_goals",
                odd=target.get("over"),
                opposite=target.get("under"),
                goals=2,
                correlation="two_goal_path",
                age=age,
                quality=data_quality,
            ))
    if minute <= _i("XBET_AUTONOMOUS_STEAM_TEAM_GOAL_MAX_MINUTE", 75):
        for side, score, name, prefix in (("home", hs, "home_total", "ИТБ1"), ("away", aws, "away_total", "ИТБ2")):
            line = score + 0.5
            target = _line(list(markets.get(name) or []), line)
            if target and target.get("over") is not None:
                add(_candidate(
                    row=market_row,
                    key=f"{name}:{line:g}",
                    pkey=f"{name}:{line}",
                    family="team_total",
                    label=f"{prefix} {line:g}",
                    strategy=f"steam_{side}_goal",
                    odd=target.get("over"),
                    opposite=target.get("under"),
                    goals=1,
                    correlation=f"{side}_next_goal",
                    age=age,
                    quality=data_quality,
                ))
    if minute <= _i("XBET_AUTONOMOUS_STEAM_BTTS_MAX_MINUTE", 75) and not (hs > 0 and aws > 0):
        btts = markets.get("btts") or {}
        if btts.get("yes") is not None:
            correlation = "home_next_goal" if hs == 0 < aws else ("away_next_goal" if aws == 0 < hs else "both_teams_score")
            add(_candidate(
                row=market_row,
                key="btts_yes",
                pkey="btts_yes:None",
                family="btts",
                label="ОЗ — Да",
                strategy="steam_btts",
                odd=btts.get("yes"),
                opposite=btts.get("no"),
                goals=1,
                correlation=correlation,
                age=age,
                quality=data_quality,
            ))
    if 0 < minute <= _i("XBET_AUTONOMOUS_STEAM_FIRST_HALF_MAX_MINUTE", 42) and not bool(match.get("is_halftime")):
        line = total + 0.5
        target = _line(list(markets.get("first_half_total") or []), line)
        if target and target.get("over") is not None:
            add(_candidate(
                row=market_row,
                key=f"first_half_total:{line:g}",
                pkey=f"first_half_total:{line}",
                family="first_half_total",
                label=f"1Т ТБ {line:g}",
                strategy="steam_goal_before_ht",
                odd=target.get("over"),
                opposite=target.get("under"),
                goals=1,
                correlation="any_next_goal",
                age=age,
                quality=data_quality,
            ))
    return out


def apply_autonomous_steam(
    decision: RouterDecision,
    record: dict[str, Any],
    market_row: dict[str, Any] | None,
    *,
    data_quality: float,
) -> RouterDecision:
    rows = build_autonomous_steam_candidates(record, market_row, data_quality=data_quality)
    if not rows:
        return decision
    winner = max(rows, key=lambda item: (item.rating, item.market_pressure_pp, -item.goals_to_win, -item.odd))
    old = decision.winner
    alternatives = ([old] if old is not None and old.key != winner.key else []) + [
        item for item in decision.alternatives if item.key != winner.key and (old is None or item.key != old.key)
    ]
    decision.status, decision.winner, decision.alternatives = "BET", winner, alternatives[:3]
    decision.reason = (
        "Сверхсильный прогруз 1xBet прошёл автономный фильтр: свежий рынок, нормальный коэффициент "
        "и устойчивое движение. GOOL/Value для этого отдельного слоя не обязательны."
    )
    return decision
